# app/bot/seller_registration_flow.py

from uuid import uuid4

import structlog
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import ContextTypes

from app.core.exceptions import DomainError, LocalizedDomainError
from app.core.utils import execute_idempotent, normalize_ethiopian_phone
from app.db.session import session_scope
from app.i18n import get_text, supported_language, translate_error
from app.models import SellerApprovalStatus
from app.services.conversation_service import (
    clear_workflow,
    get_session,
    start_workflow,
    update_workflow,
)
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    get_seller_profile,
    register_seller_application,
)

logger = structlog.get_logger()

WORKFLOW = "SELLER_REGISTRATION"


async def start_seller_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for seller registration/application.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        seller = await get_seller_profile(session, user.id)

        if seller and seller.approval_status == SellerApprovalStatus.APPROVED:
            await message.reply_text(get_text(lang, "start.seller_approved"))
            return

        if seller and seller.approval_status == SellerApprovalStatus.PENDING:
            await message.reply_text(get_text(lang, "seller.application_submitted"))
            return

        await start_workflow(
            session,
            user.id,
            WORKFLOW,
            "rules",
            {"draft_id": uuid4().hex},
        )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_text(lang, "seller.rules.agree"),
                    callback_data="seller_rules:agree",
                ),
                InlineKeyboardButton(
                    get_text(lang, "seller.rules.decline"),
                    callback_data="seller_rules:decline",
                ),
            ]
        ]
    )

    await message.reply_text(
        get_text(lang, "seller.rules"),
        reply_markup=markup,
    )


async def handle_seller_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles native Telegram contact sharing for seller phone.

    Returns True if this handler consumed the update.
    """
    message = update.effective_message
    tg_user = update.effective_user

    if not message or not tg_user or not message.contact:
        return False

    contact = message.contact

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        record = await get_session(session, user.id, WORKFLOW)
        if not record or record.state != "phone":
            return False

        if contact.user_id != tg_user.id:
            await message.reply_text(get_text(lang, "error.own_contact"))
            return True

        try:
            normalized_phone = normalize_ethiopian_phone(contact.phone_number)
        except LocalizedDomainError as exc:
            await message.reply_text(translate_error(lang, exc))
            return True

        payload = record.payload or {}

        # Existing buyers reuse full_name from buyer profile.
        # New sellers must enter full_name after phone.
        next_state = "business_name" if payload.get("full_name") else "full_name"

        await update_workflow(
            session,
            user.id,
            WORKFLOW,
            next_state,
            {"phone_number": normalized_phone},
        )

        if next_state == "business_name":
            await message.reply_text(get_text(lang, "seller.prompt.business_name"))
        else:
            await message.reply_text(get_text(lang, "prompt.full_name"))

    return True


async def handle_seller_registration_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Routes text inputs for seller registration steps.

    Returns True if this handler consumed the update.
    """
    message = update.effective_message
    tg_user = update.effective_user

    if not message or not tg_user or not message.text:
        return False

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        record = await get_session(session, user.id, WORKFLOW)
        if not record:
            return False

        text = message.text.strip()
        state = record.state

        if state == "full_name":
            full_name = " ".join(text.split())

            if len(full_name) < 3:
                await message.reply_text(get_text(lang, "error.invalid_full_name"))
                return True

            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "business_name",
                {"full_name": full_name},
            )

            await message.reply_text(get_text(lang, "seller.prompt.business_name"))
            return True

        if state == "business_name":
            business_name = " ".join(text.split())

            if len(business_name) < 2:
                await message.reply_text(get_text(lang, "error.invalid_business_name"))
                return True

            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "location",
                {"business_name": business_name},
            )

            await message.reply_text(get_text(lang, "seller.prompt.location"))
            return True

        if state == "location":
            location = " ".join(text.split())

            if len(location) < 2:
                await message.reply_text(get_text(lang, "error.invalid_location"))
                return True

            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "category",
                {"location": location},
            )

            await message.reply_text(get_text(lang, "seller.prompt.category"))
            return True

        if state == "category":
            category = " ".join(text.split())

            if len(category) < 2:
                await message.reply_text(get_text(lang, "error.invalid_category"))
                return True

            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "shop_number",
                {"product_category": category},
            )

            await message.reply_text(get_text(lang, "seller.prompt.shop_number"))
            return True

        if state == "shop_number":
            shop_number = " ".join(text.split())

            if len(shop_number) < 2:
                await message.reply_text(get_text(lang, "error.invalid_shop_number"))
                return True

            updated = await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "confirm",
                {"shop_number": shop_number},
            )

            payload = updated.payload or {}

            summary = "\n".join(
                [
                    get_text(lang, "seller.confirm.title"),
                    "",
                    f"{get_text(lang, 'seller.label_full_name')}: {payload.get('full_name', '')}",
                    f"{get_text(lang, 'seller.label_phone')}: {payload.get('phone_number', '')}",
                    f"{get_text(lang, 'seller.label_business')}: {payload.get('business_name', '')}",
                    f"{get_text(lang, 'seller.label_location')}: {payload.get('location', '')}",
                    f"{get_text(lang, 'seller.label_category')}: {payload.get('product_category', '')}",
                    f"{get_text(lang, 'seller.label_shop_number')}: {payload.get('shop_number', '')}",
                ]
            )

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            get_text(lang, "seller.confirm.submit"),
                            callback_data="seller_confirm:submit",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "seller.confirm.edit"),
                            callback_data="seller_confirm:edit",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "seller.confirm.cancel"),
                            callback_data="seller_confirm:cancel",
                        ),
                    ]
                ]
            )

            await message.reply_text(summary, reply_markup=markup)
            return True

    return False


async def handle_seller_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles callbacks for seller rules and confirmation.

    Returns True if this handler consumed the callback.
    """
    query = update.callback_query
    tg_user = update.effective_user

    if not query or not tg_user or not query.data:
        return False

    data = query.data

    if not (
        data.startswith("seller_rules:")
        or data.startswith("seller_confirm:")
    ):
        return False

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        record = await get_session(session, user.id, WORKFLOW)

        # ------------------------------------------------------------
        # Rules
        # ------------------------------------------------------------
        if data.startswith("seller_rules:"):
            action = data.split(":")[-1]
            if action == "agree":
                buyer = await get_buyer_profile(session, user.id)
                seller = await get_seller_profile(session, user.id)

                if buyer:
                    # Existing buyer reuse profile data.
                    # If a seller profile already exists, prefer its data.
                    # This preserves admin-edited seller full names.
                    await update_workflow(
                        session,
                        user.id,
                        WORKFLOW,
                        "business_name",
                        {
                            "phone_number": seller.phone_number if seller else buyer.phone_number,
                            "full_name": seller.full_name if seller else buyer.full_name,
                        },
                    )
                    await query.answer()
                    await query.message.reply_text(
                        get_text(lang, "seller.prompt.business_name")
                    )
                else:
                    # New seller:
                    # Ask for Telegram contact.
                    await update_workflow(session, user.id, WORKFLOW, "phone")

                    await query.answer()

                    markup = ReplyKeyboardMarkup(
                        [
                            [
                                KeyboardButton(
                                    get_text(lang, "buttons.share_my_phone"),
                                    request_contact=True,
                                )
                            ]
                        ],
                        resize_keyboard=True,
                        one_time_keyboard=True,
                    )

                    await query.message.reply_text(
                        get_text(lang, "prompt.share_ethiopian_phone"),
                        reply_markup=markup,
                    )

                return True

            if action == "decline":
                await clear_workflow(session, user.id, WORKFLOW)
                await query.answer()
                await query.message.reply_text(
                    get_text(lang, "operation.cancelled")
                )
                return True

            await query.answer(get_text(lang, "error.unknown_action"))
            return True

        # ------------------------------------------------------------
        # Confirmation
        # ------------------------------------------------------------
        if data.startswith("seller_confirm:"):
            if not record or record.state != "confirm":
                await query.answer(get_text(lang, "error.unknown_action"))
                return True

            payload = record.payload or {}
            action = data.split(":")[-1]

            if action == "cancel":
                await clear_workflow(session, user.id, WORKFLOW)
                await query.answer()

                if query.message:
                    try:
                        await query.edit_message_reply_markup(None)
                    except Exception:
                        pass

                await query.message.reply_text(
                    get_text(lang, "operation.cancelled"),
                    reply_markup=ReplyKeyboardRemove(),
                )

                return True

            if action == "edit":
                await update_workflow(
                    session,
                    user.id,
                    WORKFLOW,
                    "business_name",
                    {},
                )

                await query.answer()

                if query.message:
                    try:
                        await query.edit_message_reply_markup(None)
                    except Exception:
                        pass

                await query.message.reply_text(
                    get_text(lang, "seller.prompt.business_name")
                )

                return True

            if action == "submit":
                missing = [
                    key
                    for key in (
                        "phone_number",
                        "full_name",
                        "business_name",
                        "location",
                        "product_category",
                        "shop_number",
                        "draft_id",
                    )
                    if not payload.get(key)
                ]

                if missing:
                    await query.answer(
                        get_text(lang, "error.seller_fields_required"),
                        show_alert=True,
                    )
                    return True

                try:
                    async def _create():
                        profile = await register_seller_application(
                            session,
                            tg_user,
                            payload.get("phone_number"),
                            payload.get("full_name"),
                            payload["business_name"],
                            payload["location"],
                            payload["product_category"],
                            payload["shop_number"],
                        )
                        return {"seller_profile_id": str(profile.id)}

                    await execute_idempotent(
                        session,
                        f"register_seller:{user.id}:{payload['draft_id']}",
                        "SELLER_REGISTER",
                        _create,
                        user_id=user.id,
                    )

                    await clear_workflow(session, user.id, WORKFLOW)

                    await query.answer()

                    if query.message:
                        try:
                            await query.edit_message_reply_markup(None)
                        except Exception:
                            pass

                    await query.message.reply_text(
                        get_text(lang, "seller.application_submitted"),
                        reply_markup=ReplyKeyboardRemove(),
                    )

                except DomainError as exc:
                    await query.answer(translate_error(lang, exc), show_alert=True)
                except Exception:
                    logger.exception("seller_registration_failed")
                    await query.answer(get_text(lang, "error.generic"), show_alert=True)

                return True

            await query.answer(get_text(lang, "error.unknown_action"))
            return True

    return False