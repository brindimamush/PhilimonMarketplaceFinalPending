# app/bot/handlers.py
# LAYER: Presentation / Handlers
# PURPOSE: Routes Telegram updates to application services.

from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from app.bot.admin_flow import (
    handle_admin_callback,
    handle_admin_text,
)
from app.bot.buyer_registration_flow import (
    handle_buyer_contact,
    handle_buyer_registration_callback,
    handle_buyer_registration_text,
    start_buyer_registration,
)
from app.bot.buyer_request_flow import (
    handle_request_callback as handle_buyer_request_callback,
)
from app.bot.buyer_request_flow import (
    handle_request_text as handle_buyer_request_text,
)
from app.bot.seller_registration_flow import (
    handle_seller_contact,
    handle_seller_registration_callback,
    handle_seller_registration_text,
    start_seller_registration,
)
from app.bot.ui import (
    build_main_menu_markup,
    build_offer_list_markup,
    build_registration_markup,
)
from app.bot.user_dashboards import handle_dashboard_callback
from app.config.settings import get_settings
from app.core.exceptions import DomainError
from app.core.utils import execute_idempotent
from app.db.session import session_scope
from app.i18n import get_text, supported_language, translate_error
from app.models import (
    PurchaseRequest,
    SellerApprovalStatus,
    SellerOffer,
    SupportTicket,
    TicketStatus,
    UserStatus,
)
from app.services.admin_service import approve_seller_application, decline_seller_application, settle_request
from app.services.conversation_service import (
    clear_workflow,
    get_session,
    start_workflow,
)
from app.services.marketplace_service import (
    approve_request,
    decline_request,
    get_active_offers_for_request,
    select_offer,
    seller_accept_request,
    seller_reject_request,
    submit_seller_offer,
)
from app.services.support_service import create_support_ticket
from app.services.system_service import enqueue_outbox, notification_payload, notify_admins
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    get_seller_profile,
    get_user_by_telegram_id,
    set_user_language,
)

settings = get_settings()
logger = structlog.get_logger()


def _redis(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("state")


def _is_admin(tg_user) -> bool:
    return bool(tg_user and tg_user.id in settings.admin_ids)


async def _ui_lang(tg_user) -> str:
    """
    Admin interface is English.
    Regular users use their persisted language preference.
    """
    if not tg_user:
        return "en"

    if _is_admin(tg_user):
        return "en"

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_user.id)
        return supported_language(user.language if user else None)


async def _reply_error(update: Update, message: str) -> None:
    if update.callback_query:
        try:
            await update.callback_query.answer(message, show_alert=True)
        except Exception:
            pass

        if update.callback_query.message:
            try:
                await update.callback_query.message.reply_text(message)
            except Exception:
                pass

    elif update.effective_message:
        await update.effective_message.reply_text(message)

async def contact_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Routes Telegram contact-sharing messages.
    Both buyer registration and seller registration use Telegram's native
    contact button. The correct workflow is selected by inspecting the
    PostgreSQL-backed conversation state.
    """
    if await handle_buyer_contact(update, context):
        return

    if await handle_seller_contact(update, context):
        return

    tg_user = update.effective_user
    message = update.effective_message
    lang = await _ui_lang(tg_user)

    if message:
        await message.reply_text(get_text(lang, "error.unexpected_contact"))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Renders the dashboard/menu.

    /start is also the escape hatch:
    it clears active conversational workflows.
    """
    tg_user = update.effective_user

    if not tg_user or not update.effective_message:
        return

    lang = await _ui_lang(tg_user)

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        # Clear active workflows when the user returns to the dashboard.
        # Spec: /start must be a safe escape hatch from any stuck workflow.
        for workflow in (
            "BUYER_REGISTRATION",
            "BUYER_REQUEST_CREATION",
            "SELLER_REGISTRATION",
            "SELLER_OFFER",
            "SUPPORT_TICKET",
            "ADMIN_DECLINE_REASON",
            "ADMIN_SETTLE_CANCEL",
            "ADMIN_SELLER_EDIT",
            "ADMIN_SUPPORT_CLOSE",
            "ADMIN_SUPPORT_RESPOND",
            "ADMIN_SUSPEND_REASON",
        ):
            await clear_workflow(session, user.id, workflow)
            
        if user.status == UserStatus.SUSPENDED:
            await update.effective_message.reply_text(
                get_text(user.language, "suspended.message")
            )
            return

        buyer = await get_buyer_profile(session, user.id)
        seller = await get_seller_profile(session, user.id)
        # Spec:
        # If the user is not registered yet, show registration options.
        if not buyer and not seller:
            await update.effective_message.reply_text(
                get_text(lang, "start.welcome"),
                reply_markup=build_registration_markup(lang),
            )
            return
        seller_approved = bool(
            seller and seller.approval_status == SellerApprovalStatus.APPROVED
        )

        markup = build_main_menu_markup(
            has_buyer=bool(buyer),
            seller_approved=seller_approved,
            is_admin=_is_admin(tg_user),
            show_seller_registration=not seller_approved,
        )

        await update.effective_message.reply_text(
            get_text(lang, "start.dashboard"),
            reply_markup=markup,
        )

async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lets the user choose English or Amharic.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    lang = await _ui_lang(tg_user)

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text(lang, "language.english"),
                    callback_data="lang:set:en",
                ),
                InlineKeyboardButton(
                    get_text(lang, "language.amharic"),
                    callback_data="lang:set:am",
                ),
            ]
        ]
    )

    await message.reply_text(
        get_text(lang, "language.choose"),
        reply_markup=markup,
    )

async def new_request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Placeholder for full request creation flow.

    Full Phase 3 implementation must add:
    image -> description -> quantity -> confirmation -> submit.
    """
    lang = await _ui_lang(update.effective_user)
    await update.effective_message.reply_text(get_text(lang, "prompt.send_item_image"))


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Starts support workflow.

    Suspended users must be able to use this command.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = "en" if _is_admin(tg_user) else supported_language(user.language)

        await start_workflow(
            session,
            user.id,
            "SUPPORT_TICKET",
            "description",
            {"draft_id": uuid4().hex},
        )

    await message.reply_text(get_text(lang, "prompt.support_description"))


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Routes text input based on workflow state.

    Critical workflow state is stored in PostgreSQL conversation_sessions.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message or not message.text:
        return

    # Buyer request workflow consumes its own text steps.
    if await handle_buyer_request_text(update, context):
        return
    # Registration and admin workflow text routers.
    if await handle_buyer_registration_text(update, context):
        return

    if await handle_seller_registration_text(update, context):
        return

    if await handle_admin_text(update, context):
        return
    
    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = "en" if _is_admin(tg_user) else supported_language(user.language)

        # ------------------------------------------------------------
        # Support flow
        # ------------------------------------------------------------
        support_session = await get_session(session, user.id, "SUPPORT_TICKET")
                 # ------------------------------------------------------------
         # User reply to an existing open / in-progress ticket
         # ------------------------------------------------------------
        open_ticket = await session.scalar(
             select(SupportTicket)
             .where(
                 SupportTicket.user_id == user.id,
                 SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]),
             )
             .order_by(SupportTicket.created_at.desc())
             .limit(1)
         )
        if open_ticket and not support_session:
             try:
                 # Append user reply to the ticket description / conversation
                 open_ticket.description = (
                     (open_ticket.description or "")
                     + f"\n\n--- User reply ---\n{message.text.strip()}"
                 )
                 open_ticket.status = TicketStatus.OPEN  # re-open for admin attention
                 await session.flush()

                 # Notify admins of the reply
                 admin_text = (
                     f"💬 User replied on ticket {open_ticket.ticket_number}:\n\n"
                     f"{message.text.strip()}"
                 )
                 buttons = [
                     [
                         {"text": "View Ticket", "callback_data": f"support:view:{open_ticket.id}"},
                         {"text": "Respond", "callback_data": f"support:respond:{open_ticket.id}"},
                     ]
                 ]
                 await notify_admins(session, text=admin_text, buttons=buttons)

                 await message.reply_text(
                     get_text(lang, "support.ticket_created", ticket_number=open_ticket.ticket_number)
                 )
             except Exception:
                 logger.exception("user_ticket_reply_failed")
                 await message.reply_text(get_text(lang, "error.generic"))
             return
        
        if support_session:
            payload = support_session.payload or {}
            draft_id = payload.get("draft_id") or str(support_session.id)

            try:
                async def _create_ticket():
                    ticket = await create_support_ticket(session, tg_user, message.text)
                    return {"ticket_number": ticket.ticket_number}

                result = await execute_idempotent(
                    session,
                    f"create_support_ticket:{user.id}:{draft_id}",
                    "SUPPORT_CREATE",
                    _create_ticket,
                    user_id=user.id,
                )

                if result.get("status") == "processing":
                    await message.reply_text(get_text(lang, "error.generic"))
                    return

                await clear_workflow(session, user.id, "SUPPORT_TICKET")
                await message.reply_text(
                    get_text(
                        lang,
                        "support.ticket_created",
                        ticket_number=result.get("ticket_number", ""),
                    )
                )
            except DomainError as exc:
                await message.reply_text(translate_error(lang, exc))
            except Exception:
                logger.exception("support_ticket_failed")
                await message.reply_text(get_text(lang, "error.generic"))
            return

        # ------------------------------------------------------------
        # Admin flows
        # ------------------------------------------------------------
        if _is_admin(tg_user):
            decline_session = await get_session(session, user.id, "ADMIN_DECLINE_REASON")
            if decline_session:
                try:
                    request_id = UUID(decline_session.payload["request_id"])

                    await execute_idempotent(
                        session,
                        f"decline_request:{request_id}",
                        "REQUEST_DECLINE",
                        lambda: decline_request(session, tg_user, request_id, message.text),
                        request_id=request_id,
                        user_id=user.id,
                    )

                    await clear_workflow(session, user.id, "ADMIN_DECLINE_REASON")
                    await message.reply_text("Request declined.")
                except DomainError as exc:
                    await message.reply_text(translate_error("en", exc))
                except Exception:
                    logger.exception("admin_decline_failed")
                    await message.reply_text("Decline failed.")

                return

            settle_session = await get_session(session, user.id, "ADMIN_SETTLE_CANCEL")
            if settle_session:
                try:
                    request_id = UUID(settle_session.payload["request_id"])

                    await execute_idempotent(
                        session,
                        f"settle_request:canceled:{request_id}",
                        "REQUEST_SETTLEMENT",
                        lambda: settle_request(session, tg_user, request_id, "canceled", message.text),
                        request_id=request_id,
                        user_id=user.id,
                    )

                    await clear_workflow(session, user.id, "ADMIN_SETTLE_CANCEL")
                    await message.reply_text("Request canceled.")
                except DomainError as exc:
                    await message.reply_text(translate_error("en", exc))
                except Exception:
                    logger.exception("admin_settle_cancel_failed")
                    await message.reply_text("Cancellation failed.")

                return

        # ------------------------------------------------------------
        # Seller offer price flow
        # ------------------------------------------------------------
        seller_session = await get_session(session, user.id, "SELLER_OFFER")
        if seller_session:
            try:
                request_id = UUID(seller_session.payload["request_id"])

                await execute_idempotent(
                    session,
                    f"submit_offer:{request_id}:{tg_user.id}",
                    "OFFER_SUBMIT",
                    lambda: submit_seller_offer(session, tg_user, request_id, message.text),
                    request_id=request_id,
                    user_id=user.id,
                )

                await clear_workflow(session, user.id, "SELLER_OFFER")
                await message.reply_text(get_text(lang, "offer.submitted"))
            except DomainError as exc:
                await message.reply_text(translate_error(lang, exc))
            except Exception:
                logger.exception("seller_offer_failed")
                await message.reply_text(get_text(lang, "error.generic"))

            return

        await message.reply_text(get_text(lang, "error.use_start"))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Central router for inline buttons.

    Every state-changing callback is wrapped in idempotency protection.
    """
    query = update.callback_query
    tg_user = update.effective_user

    if not query or not tg_user:
        return

    data = query.data or ""
    lang = await _ui_lang(tg_user)

    # ------------------------------------------------------------
    # Registration / helper callbacks
    # ------------------------------------------------------------
    try:
        if data == "register:buyer":
            await query.answer()
            await start_buyer_registration(update, context)
            return

        if data == "register:seller":
            await query.answer()
            await start_seller_registration(update, context)
            return

        if data == "support:open":
            await query.answer()
            await support_cmd(update, context)
            return

        if data == "language:open":
            await query.answer()
            await language_cmd(update, context)
            return

        if await handle_buyer_registration_callback(update, context):
            return

        if await handle_seller_registration_callback(update, context):
            return

        if await handle_admin_callback(update, context):
            return

    except DomainError as exc:
        await _reply_error(update, translate_error(lang, exc))
        return
    except Exception:
        logger.exception("callback_failed", data=data)
        await _reply_error(update, get_text(lang, "error.generic"))
        return
    # ------------------------------------------------------------
    # Buyer request draft callbacks
    # ------------------------------------------------------------
    if await handle_buyer_request_callback(update, context):
            return

    # ------------------------------------------------------------
    # User dashboard callbacks
    # ------------------------------------------------------------
    if await handle_dashboard_callback(update, context):
            return
    redis = _redis(context)

    try:
        # ------------------------------------------------------------
        # Close UI
        # ------------------------------------------------------------
        if data == "ui:close":
            await query.answer()
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass
            return
        # ------------------------------------------------------------
        # Language selection
        # ------------------------------------------------------------
        if data.startswith("lang:set:"):
            code = data.split(":")[-1]
            async with session_scope() as session:
                user = await set_user_language(session, tg_user, code)
                lang = supported_language(user.language)
                buyer = await get_buyer_profile(session, user.id)
                seller = await get_seller_profile(session, user.id)

            await query.answer(get_text(lang, "language.updated"))

            if query.message:
                await query.message.reply_text(get_text(lang, "language.updated"))

                # ---- Re-render the correct menu in the NEW language ----
                if not buyer and not seller:
                    await query.message.reply_text(
                        get_text(lang, "start.welcome"),
                        reply_markup=build_registration_markup(lang),
                    )
                else:
                    seller_approved = bool(
                        seller and seller.approval_status == SellerApprovalStatus.APPROVED
                    )
                    markup = build_main_menu_markup(
                        has_buyer=bool(buyer),
                        seller_approved=seller_approved,
                        is_admin=_is_admin(tg_user),
                        show_seller_registration=not seller_approved,
                    )
                    await query.message.reply_text(
                        get_text(lang, "start.dashboard"),
                        reply_markup=markup,
                    )
            return
        # ------------------------------------------------------------
        # Admin approve request
        # ------------------------------------------------------------
        if data.startswith("request:approve:"):
            if not _is_admin(tg_user):
                raise DomainError("Not authorized.")

            request_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                await execute_idempotent(
                    session,
                    f"approve_request:{request_id}",
                    "REQUEST_APPROVE",
                    lambda: approve_request(session, tg_user, request_id),
                    request_id=request_id,
                )

            await query.answer("Approved")

            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass

                await query.message.reply_text("Request approved.")

            return
        # ------------------------------------------------------------
        # Admin decline request: ask reason
        # ------------------------------------------------------------
        if data.startswith("request:decline:"):
            if not _is_admin(tg_user):
                raise DomainError("Not authorized.")

            request_id = data.split(":")[-1]

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)

                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_DECLINE_REASON",
                    "reason",
                    {"request_id": request_id},
                )

            await query.answer()

            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass

                await query.message.reply_text(
                    "Please enter the reason for declining this request."
                )

            return
            # ------------------------------------------------------------
        # Seller accept request
        # ------------------------------------------------------------
        if data.startswith("seller:accept:"):
            request_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                seller_user = await get_or_create_user(session, tg_user)

                await execute_idempotent(
                    session,
                    f"accept_request:{request_id}:{tg_user.id}",
                    "SELLER_ACCEPT",
                    lambda: seller_accept_request(session, tg_user, request_id),
                    request_id=request_id,
                    user_id=seller_user.id,
                )

                await start_workflow(
                    session,
                    seller_user.id,
                    "SELLER_OFFER",
                    "price",
                    {"request_id": str(request_id)},
                )

            await query.answer()

            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass

                await query.message.reply_text(
                    get_text(lang, "seller_request.accepted_enter_price")
                )

            return

        # ------------------------------------------------------------
        # Seller reject request: ask confirmation
        # ------------------------------------------------------------
        if data.startswith("seller:reject:"):
            request_id = data.split(":")[-1]

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            get_text(lang, "buttons.yes_reject"),
                            callback_data=f"seller:reject_confirm:{request_id}",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "buttons.cancel"),
                            callback_data=f"seller:reject_cancel:{request_id}",
                        ),
                    ]
                ]
            )

            await query.answer()
            if query.message:
                await query.message.reply_text(
                    get_text(lang, "seller_request.reject_confirm"),
                    reply_markup=markup,
                )
            return
        # ------------------------------------------------------------
        if data.startswith("seller:reject_confirm:"):
            request_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                await execute_idempotent(
                    session,
                    f"reject_request:{request_id}:{tg_user.id}",
                    "SELLER_REJECT",
                    lambda: seller_reject_request(session, tg_user, request_id),
                    request_id=request_id,
                )

            await query.answer()

            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass

                await query.message.reply_text(
                    get_text(lang, "seller_request.rejected"),
                    reply_markup=None,
                )

            return

        if data.startswith("seller:reject_cancel:"):
            await query.answer(get_text(lang, "seller_request.rejection_cancelled"))
            return

        # ------------------------------------------------------------
        # Buyer view offer list
        # ------------------------------------------------------------
        if data.startswith("request:offers:"):
            request_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                request, offers = await get_active_offers_for_request(
                    session,
                    tg_user,
                    request_id,
                )

                if not offers:
                    await query.message.reply_text(get_text(lang, "offer.no_active"))
                else:
                    markup = build_offer_list_markup(offers, lang)
                    await query.message.reply_photo(
                        photo=request.image_file_id,
                        caption=get_text(
                            lang,
                            "offer.list_caption",
                            request_number=request.request_number,
                        ),
                        reply_markup=markup,
                    )

            await query.answer()
            return
            # ------------------------------------------------------------
        # Buyer offer confirmation
        # Spec:
        # Buyer must see the exact price before final selection.
        # ------------------------------------------------------------
        if data.startswith("offer:confirm:"):
            offer_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                user = await get_or_create_user(session, tg_user)

                offer = await session.get(SellerOffer, offer_id)
                if not offer:
                    await query.answer(
                        get_text(lang, "error.offer_not_found"),
                        show_alert=True,
                    )
                    return

                request = await session.get(PurchaseRequest, offer.request_id)
                if not request or request.buyer_id != user.id:
                    await query.answer(
                        get_text(lang, "error.request_not_yours"),
                        show_alert=True,
                    )
                    return

                text = "\n".join(
                    [
                        get_text(lang, "offer.select_question_title"),
                        "",
                        f"{get_text(lang, 'offer.price_label')}: "
                        f"{offer.price:,.2f} {offer.currency}",
                    ]
                )

                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                get_text(lang, "buttons.confirm"),
                                callback_data=f"offer:select:{offer.id}",
                            ),
                            InlineKeyboardButton(
                                get_text(lang, "buttons.choose_another"),
                                callback_data=f"request:offers:{request.id}",
                            ),
                        ]
                    ]
                )

                await query.answer()

                if query.message:
                    await query.message.reply_text(text, reply_markup=markup)

            return
        # ------------------------------------------------------------
        # Buyer selects offer
        # ------------------------------------------------------------
        if data.startswith("offer:select:"):
            offer_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                await execute_idempotent(
                    session,
                    f"select_offer:{offer_id}",
                    "OFFER_SELECT",
                    lambda: select_offer(session, tg_user, offer_id),
                )

            await query.answer()
            if query.message:
                await query.message.reply_text(
                    get_text(lang, "offer.selected"),
                    reply_markup=None,
                )
            return

        # ------------------------------------------------------------
        # Admin settlement actions
        # ------------------------------------------------------------
        if data.startswith("settle:settled:") or data.startswith("settle:pending:"):
            if not _is_admin(tg_user):
                raise DomainError("Not authorized.")

            action = data.split(":")[1]
            request_id = UUID(data.split(":")[2])

            async with session_scope() as session:
                await execute_idempotent(
                    session,
                    f"settle_request:{action}:{request_id}",
                    "REQUEST_SETTLEMENT",
                    lambda: settle_request(session, tg_user, request_id, action, ""),
                    request_id=request_id,
                )

            await query.answer("Done")

            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass

                await query.message.reply_text(f"Settlement action applied: {action}.")

            return

        if data.startswith("settle:canceled:"):
            if not _is_admin(tg_user):
                raise DomainError("Not authorized.")

            request_id = data.split(":")[-1]

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)

                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SETTLE_CANCEL",
                    "reason",
                    {"request_id": request_id},
                )

            await query.answer()

            if query.message:
                await query.message.reply_text(
                    "Please enter the reason for cancellation."
                )

            return

            # ------------------------------------------------------------
        # Admin Seller Application Management
        # ------------------------------------------------------------
        if data.startswith("seller_app:approve:") or data.startswith("seller_app:decline:"):
            if not _is_admin(tg_user):
                raise DomainError("Not authorized.")
            
            action = data.split(":")[1]
            target_user_id = UUID(data.split(":")[-1])
            
            async with session_scope() as session:
                if action == "approve":
                    await execute_idempotent(
                        session, f"approve_seller:{target_user_id}", "SELLER_APPROVE",
                        lambda: approve_seller_application(session, tg_user, target_user_id),
                        user_id=target_user_id
                    )
                    await query.answer("Seller Approved")

                    if query.message:
                        try:
                            await query.edit_message_reply_markup(None)
                        except Exception:
                            pass

                        await query.message.reply_text("✅ Seller application approved.")
                elif action == "decline":
                    await execute_idempotent(
                        session, f"decline_seller:{target_user_id}", "SELLER_DECLINE",
                        lambda: decline_seller_application(session, tg_user, target_user_id),
                        user_id=target_user_id
                    )
                    await query.answer("Seller Declined")

                    if query.message:
                        try:
                            await query.edit_message_reply_markup(None)
                        except Exception:
                            pass

                        await query.message.reply_text("❌ Seller application declined.")
            return

        await query.answer(get_text(lang, "error.unknown_action"))

    except DomainError as exc:
        await _reply_error(update, translate_error(lang, exc))

    except Exception:
        logger.exception("callback_failed", data=data)
        await _reply_error(update, get_text(lang, "error.generic"))