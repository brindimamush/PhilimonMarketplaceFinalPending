# app/bot/admin_flow.py

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config.settings import get_settings
from app.core.exceptions import DomainError
from app.core.utils import execute_idempotent
from app.db.session import session_scope
from app.models import (
    PurchaseRequest,
    RequestStatus,
    SellerApprovalStatus,
    SellerOffer,
    SellerProfile,
    SupportTicket,
    TicketStatus,
    User,
    UserStatus,
    user,
)
from app.services.admin_service import (
    admin_search,
    close_support_ticket,
    get_admin_dashboard_counts,
    get_admin_requests_page,
    get_admin_users_page,
    lift_user_suspension_by_telegram_id,
    suspend_user_by_telegram_id,
    update_seller_application_field,
)
from app.services.conversation_service import (
    clear_workflow,
    get_session,
    start_workflow,
    update_workflow,
)
from app.services.support_service import respond_support_ticket
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    get_seller_profile,
)

settings = get_settings()
logger = structlog.get_logger()

PAGE_SIZE = 10

SELLER_EDIT_FIELDS = [
    "full_name",
    "business_name",
    "location",
    "product_category",
    "shop_number",
]


def _is_admin(tg_user) -> bool:
    return bool(tg_user and tg_user.id in settings.admin_ids)


def _paginate(page: int, page_size: int, total: int) -> tuple[int, int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 10))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    return page, total_pages, (page - 1) * page_size


async def _reply_or_edit(update: Update, text: str, markup=None) -> None:
    query = update.callback_query

    if query and query.message:
        try:
            await query.edit_message_text(text, reply_markup=markup)
            return
        except Exception:
            pass

    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=markup)


def _add_nav_rows(rows: list, page: int, total_pages: int, prefix: str) -> None:
    nav = []

    if page > 1:
        nav.append(
            InlineKeyboardButton(
                "Previous",
                callback_data=f"{prefix}:{page - 1}",
            )
        )

    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                "Next",
                callback_data=f"{prefix}:{page + 1}",
            )
        )

    if nav:
        rows.append(nav)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin dashboard entrypoint.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    if not _is_admin(tg_user):
        await message.reply_text("Not authorized.")
        return

    async with session_scope() as session:
        counts = await get_admin_dashboard_counts(session)

    text = "\n".join(
        [
            "Admin Dashboard",
            "",
            f"Users: {counts['users_total']}",
            f"Pending Buyer Requests: {counts['requests_pending']}",
            f"Pending Seller Applications: {counts['seller_applications_pending']}",
            f"Suspended Users: {counts['suspended_users']}",
            f"Open Support Tickets: {counts['support_open']}",
        ]
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Users", callback_data="admin:users:1"),
                InlineKeyboardButton("Requests", callback_data="admin:requests:1"),
            ],
            [
                InlineKeyboardButton("Seller Applications", callback_data="admin:seller_apps:1"),
                InlineKeyboardButton("Support Tickets", callback_data="admin:support:1"),
            ],
            [
                InlineKeyboardButton("Suspended Users", callback_data="admin:suspended:1"),
            ],
        ]
    )

    await message.reply_text(text, reply_markup=markup)


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin search command.

    Usage:
    /search username|phone|telegram id|request number
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    if not _is_admin(tg_user):
        await message.reply_text("Not authorized.")
        return

    query_text = " ".join(context.args or []).strip()

    if not query_text:
        await message.reply_text("Usage: /search <username|phone|telegram id|request>")
        return

    async with session_scope() as session:
        user_results, request_results = await admin_search(session, query_text)

    rows = []

    for item in user_results:
        rows.append(
            [
                InlineKeyboardButton(
                    f"👤 {item['label']}",
                    callback_data=f"admin:user:view:{item['id']}",
                )
            ]
        )

    for request in request_results:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📦 {request.request_number} • {request.status.value}",
                    callback_data=f"admin:request:view:{request.id}",
                )
            ]
        )

    if not rows:
        await message.reply_text("No results found.")
        return

    rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

    await message.reply_text(
        "Search results:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles admin text workflows:
    - seller application editing
    - support ticket closure
    - suspension reason
    """
    message = update.effective_message
    tg_user = update.effective_user

    if not message or not tg_user or not message.text:
        return False

    if not _is_admin(tg_user):
        return False

    text = message.text.strip()

    async with session_scope() as session:
        admin_user = await get_or_create_user(session, tg_user)

         # ------------------------------------------------------------
        # Admin support ticket response
        # ------------------------------------------------------------
        respond_session = await get_session(session, admin_user.id, "ADMIN_SUPPORT_RESPOND")
        if respond_session:
            ticket_id = UUID(respond_session.payload["ticket_id"])
            try:
                import time as _time
                # Unique key per response so admins can respond multiple times
                idem_key = f"respond_support_ticket:{ticket_id}:{int(_time.time() * 1000)}"
                await execute_idempotent(
                    session,
                    idem_key,
                    "SUPPORT_RESPOND",
                    lambda: respond_support_ticket(session, tg_user, ticket_id, text),
                    user_id=admin_user.id,
                )
                await clear_workflow(session, admin_user.id, "ADMIN_SUPPORT_RESPOND")
                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "View Ticket",
                                callback_data=f"support:view:{ticket_id}",
                            ),
                            InlineKeyboardButton(
                                "Close Ticket",
                                callback_data=f"support:close:{ticket_id}",
                            ),
                        ]
                    ]
                )
                await message.reply_text("Response saved.", reply_markup=markup)
            except DomainError as exc:
                await message.reply_text(str(exc))
            except Exception:
                logger.exception("admin_support_respond_failed")
                await message.reply_text("Support response failed.")
            finally:
                # Always clear the workflow so admin is not stuck in a loop
                try:
                    await clear_workflow(session, admin_user.id, "ADMIN_SUPPORT_RESPOND")
                except Exception:
                    pass
            return True

        # ------------------------------------------------------------
        # Admin seller application edit
        # ------------------------------------------------------------
        edit_session = await get_session(session, admin_user.id, "ADMIN_SELLER_EDIT")
        if edit_session:
            field = edit_session.state

            if field not in SELLER_EDIT_FIELDS:
                return False

            target_user_id = UUID(edit_session.payload["target_user_id"])

            try:
                await update_seller_application_field(
                    session,
                    tg_user,
                    target_user_id,
                    field,
                    text,
                )
            except DomainError as exc:
                await message.reply_text(str(exc))
                return True

            next_index = SELLER_EDIT_FIELDS.index(field) + 1

            if next_index < len(SELLER_EDIT_FIELDS):
                next_field = SELLER_EDIT_FIELDS[next_index]

                await update_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SELLER_EDIT",
                    next_field,
                )

                await message.reply_text(
                    f"Enter {next_field.replace('_', ' ')}."
                )
                return True

            seller = await get_seller_profile(session, target_user_id)
            await clear_workflow(session, admin_user.id, "ADMIN_SELLER_EDIT")

            summary = "\n".join(
                [
                    "Seller application updated.",
                    "",
                    f"Full Name: {seller.full_name if seller else ''}",
                    f"Business: {seller.business_name if seller else ''}",
                    f"Location: {seller.location if seller else ''}",
                    f"Category: {seller.product_category if seller else ''}",
                    f"Shop Number: {seller.shop_number if seller else ''}",
                    f"Phone: {seller.phone_number if seller else ''}",
                ]
            )

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Approve",
                            callback_data=f"seller_app:approve:{target_user_id}",
                        ),
                        InlineKeyboardButton(
                            "Decline",
                            callback_data=f"seller_app:decline:{target_user_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "Edit Again",
                            callback_data=f"seller_app:edit:{target_user_id}",
                        )
                    ],
                ]
            )

            await message.reply_text(summary, reply_markup=markup)
            return True

        # ------------------------------------------------------------
        # Admin support ticket closure
        # ------------------------------------------------------------
        close_session = await get_session(session, admin_user.id, "ADMIN_SUPPORT_CLOSE")
        if close_session:
            ticket_id = UUID(close_session.payload["ticket_id"])

            try:
                await execute_idempotent(
                    session,
                    f"close_support_ticket:{ticket_id}",
                    "SUPPORT_CLOSE",
                    lambda: close_support_ticket(session, tg_user, ticket_id, text),
                    user_id=admin_user.id,
                )

                await clear_workflow(session, admin_user.id, "ADMIN_SUPPORT_CLOSE")
                await message.reply_text("Support ticket closed.")
            except DomainError as exc:
                await message.reply_text(str(exc))
            except Exception:
                logger.exception("admin_support_close_failed")
                await message.reply_text("Ticket closure failed.")

            return True

        # ------------------------------------------------------------
        # Admin suspension reason
        # ------------------------------------------------------------
    suspend_session = await get_session(session, admin_user.id, "ADMIN_SUSPEND_REASON")
    if suspend_session:
        target_telegram_id = int(suspend_session.payload["telegram_id"])

        # Prevent self-suspension before calling the service.
        if target_telegram_id == tg_user.id:
            await clear_workflow(session, admin_user.id, "ADMIN_SUSPEND_REASON")
            await message.reply_text("You cannot suspend your own account.")
            return True

        try:
            await suspend_user_by_telegram_id(
                session,
                tg_user,
                target_telegram_id,
                text,
            )
            await clear_workflow(session, admin_user.id, "ADMIN_SUSPEND_REASON")
            await message.reply_text("User suspended.")
        except DomainError as exc:
            await message.reply_text(str(exc))
        except Exception:
            logger.exception("admin_suspend_failed")
            await message.reply_text("Suspension failed.")

        return True

    return False


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles admin dashboard callbacks.
    """
    query = update.callback_query
    tg_user = update.effective_user

    if not query or not tg_user or not query.data:
        return False

    if not _is_admin(tg_user):
        return False

    data = query.data

    try:
        # ------------------------------------------------------------
        # Lists
        # ------------------------------------------------------------
        if data.startswith("admin:users:"):
            page = int(data.split(":")[-1])
            await _render_users_page(update, page, None, "admin:users")
            await query.answer()
            return True

        if data.startswith("admin:suspended:"):
            page = int(data.split(":")[-1])
            await _render_users_page(update, page, UserStatus.SUSPENDED, "admin:suspended")
            await query.answer()
            return True

        if data.startswith("admin:requests:"):
            page = int(data.split(":")[-1])
            await _render_requests_page(update, page)
            await query.answer()
            return True

        if data.startswith("admin:seller_apps:"):
            page = int(data.split(":")[-1])
            await _render_seller_apps_page(update, page)
            await query.answer()
            return True

        if data.startswith("admin:support:"):
            page = int(data.split(":")[-1])
            await _render_support_page(update, page)
            await query.answer()
            return True

        # ------------------------------------------------------------
        # Details
        # ------------------------------------------------------------
        if data.startswith("admin:user:view:"):
            user_id = UUID(data.split(":")[-1])
            await _render_user_detail(update, user_id)
            await query.answer()
            return True

        if data.startswith("admin:request:view:"):
            request_id = UUID(data.split(":")[-1])
            await _render_request_detail(update, request_id)
            await query.answer()
            return True

        if data.startswith("admin:seller_app:view:"):
            target_user_id = UUID(data.split(":")[-1])
            await _render_seller_app_detail(update, target_user_id)
            await query.answer()
            return True

        if data.startswith("support:view:"):
            ticket_id = UUID(data.split(":")[-1])
            await _render_ticket_detail(update, ticket_id)
            await query.answer()
            return True

        if data.startswith("support:respond:"):
            ticket_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)
                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SUPPORT_RESPOND",
                    "response",
                    {"ticket_id": str(ticket_id)},
                )

            await query.answer()
            if query.message:
                await query.message.reply_text("Enter the admin response.")

            return True

        # ------------------------------------------------------------
        # Actions
        # ------------------------------------------------------------
        if data.startswith("admin:user:suspend:"):
            user_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)
                target = await session.get(User, user_id)

                if not target:
                    await query.answer("User not found", show_alert=True)
                    return True

        # Prevent admin from suspending themselves.
                if target.id == admin_user.id or target.telegram_id == tg_user.id:
                    await query.answer(
                        "You cannot suspend your own account.",
                        show_alert=True,
                    )
                    return True

                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SUSPEND_REASON",
                    "reason",
                    {"telegram_id": str(target.telegram_id)},
                )

            await query.answer()
            if query.message:
                await query.message.reply_text("Enter suspension reason.")

            return True

        if data.startswith("admin:user:lift:"):
            user_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)
                target = await session.get(User, user_id)

                if not target:
                    await query.answer("User not found", show_alert=True)
                    return True

                await execute_idempotent(
                    session,
                    f"lift_suspension:{target.telegram_id}",
                    "USER_UNSUSPEND",
                    lambda: lift_user_suspension_by_telegram_id(
                        session,
                        tg_user,
                        target.telegram_id,
                    ),
                    user_id=admin_user.id,
                )

            await query.answer("Suspension lifted")

            if query.message:
                await query.message.reply_text("Suspension lifted.")

            return True

        if data.startswith("seller_app:edit:"):
            target_user_id = data.split(":")[-1]

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)
                seller = await session.scalar(
                    select(SellerProfile).where(
                        SellerProfile.user_id == UUID(target_user_id)
                    )
                )

                if not seller:
                    await query.answer("Seller application not found", show_alert=True)
                    return True

                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SELLER_EDIT",
                    "full_name",
                    {"target_user_id": target_user_id},
                )

            await query.answer()

            if query.message:
                await query.message.reply_text(
                    f"Enter full_name. Current: {seller.full_name}"
                )

            return True

        if data.startswith("support:close:"):
            ticket_id = UUID(data.split(":")[-1])

            async with session_scope() as session:
                admin_user = await get_or_create_user(session, tg_user)

                await start_workflow(
                    session,
                    admin_user.id,
                    "ADMIN_SUPPORT_CLOSE",
                    "solution",
                    {"ticket_id": str(ticket_id)},
                )

            await query.answer()

            if query.message:
                await query.message.reply_text("Enter the solution provided to the user.")

            return True

    except DomainError as exc:
        await query.answer(str(exc), show_alert=True)
        return True
    except Exception:
        logger.exception("admin_callback_failed", data=data)
        await query.answer("Admin action failed", show_alert=True)
        return True

    return False


async def _render_users_page(
    update: Update,
    page: int,
    status: UserStatus | None,
    prefix: str,
) -> None:
    async with session_scope() as session:
        page, total_pages, total, items = await get_admin_users_page(
            session,
            page,
            PAGE_SIZE,
            status,
        )

    rows = []

    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    item["label"],
                    callback_data=f"admin:user:view:{item['id']}",
                )
            ]
        )

    _add_nav_rows(rows, page, total_pages, prefix)
    rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

    title = "Suspended Users" if status == UserStatus.SUSPENDED else "Users"

    await _reply_or_edit(
        update,
        f"{title} — Page {page}/{total_pages}\nTotal: {total}",
        InlineKeyboardMarkup(rows),
    )


async def _render_requests_page(update: Update, page: int) -> None:
    async with session_scope() as session:
        page, total_pages, total, items = await get_admin_requests_page(
            session,
            page,
            PAGE_SIZE,
        )

    rows = []

    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    item["label"],
                    callback_data=f"admin:request:view:{item['id']}",
                )
            ]
        )

    _add_nav_rows(rows, page, total_pages, "admin:requests")
    rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

    await _reply_or_edit(
        update,
        f"Requests — Page {page}/{total_pages}\nTotal: {total}",
        InlineKeyboardMarkup(rows),
    )


async def _render_seller_apps_page(update: Update, page: int) -> None:
    async with session_scope() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(SellerProfile)
            .where(SellerProfile.approval_status == SellerApprovalStatus.PENDING)
        ) or 0

        page, total_pages, offset = _paginate(page, PAGE_SIZE, total)

        profiles = (
            await session.execute(
                select(SellerProfile)
                .where(SellerProfile.approval_status == SellerApprovalStatus.PENDING)
                .order_by(SellerProfile.created_at.desc())
                .offset(offset)
                .limit(PAGE_SIZE)
            )
        ).scalars().all()

    rows = []

    for profile in profiles:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{profile.business_name} • {profile.full_name}",
                    callback_data=f"admin:seller_app:view:{profile.user_id}",
                )
            ]
        )

    _add_nav_rows(rows, page, total_pages, "admin:seller_apps")
    rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

    await _reply_or_edit(
        update,
        f"Pending Seller Applications — Page {page}/{total_pages}\nTotal: {total}",
        InlineKeyboardMarkup(rows),
    )


async def _render_support_page(update: Update, page: int) -> None:
    async with session_scope() as session:
        statuses = [TicketStatus.OPEN, TicketStatus.IN_PROGRESS]

        total = await session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(SupportTicket.status.in_(statuses))
        ) or 0

        page, total_pages, offset = _paginate(page, PAGE_SIZE, total)

        tickets = (
            await session.execute(
                select(SupportTicket)
                .where(SupportTicket.status.in_(statuses))
                .order_by(SupportTicket.created_at.desc())
                .offset(offset)
                .limit(PAGE_SIZE)
            )
        ).scalars().all()

    rows = []

    for ticket in tickets:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{ticket.ticket_number} • {ticket.status.value}",
                    callback_data=f"support:view:{ticket.id}",
                )
            ]
        )

    _add_nav_rows(rows, page, total_pages, "admin:support")
    rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

    await _reply_or_edit(
        update,
        f"Support Tickets — Page {page}/{total_pages}\nTotal: {total}",
        InlineKeyboardMarkup(rows),
    )


async def _render_user_detail(update: Update, user_id: UUID) -> None:
    query = update.callback_query

    async with session_scope() as session:
        user = await session.get(User, user_id)

        if not user:
            if query:
                await query.answer("User not found", show_alert=True)
            return

        buyer = await get_buyer_profile(session, user.id)
        seller = await get_seller_profile(session, user.id)

        lines = [
            "User Details",
            "",
            f"Telegram ID: {user.telegram_id}",
            f"Username: @{user.username}" if user.username else "Username: -",
            f"Name: {user.first_name or ''} {user.last_name or ''}".strip(),
            f"Status: {user.status.value}",
            f"Language: {user.language}",
            "",
            f"Buyer: {'Yes' if buyer else 'No'}",
            f"Seller: {'Yes' if seller else 'No'}",
        ]

        if seller:
            lines.extend(
                [
                    "",
                    f"Seller Approval: {seller.approval_status.value}",
                    f"Business: {seller.business_name}",
                    f"Location: {seller.location}",
                    f"Category: {seller.product_category}",
                    f"Shop Number: {seller.shop_number}",
                ]
            )
        viewer = update.effective_user
        is_self = bool(viewer and user.telegram_id == viewer.id)

        if is_self:
            lines.append("")
            lines.append("Self-suspension is disabled.")

        rows = []

        if user.status == UserStatus.ACTIVE and not is_self:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Suspend",
                        callback_data=f"admin:user:suspend:{user.id}",
                    )
                ]
            
        
            )
        elif user.status != UserStatus.ACTIVE:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Lift Suspension",
                        callback_data=f"admin:user:lift:{user.id}",
                )
                ]
            )
        
    

        rows.append([InlineKeyboardButton("Back to Users", callback_data="admin:users:1")])

        if query and query.message:
            await query.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(rows),
            )


async def _render_request_detail(update: Update, request_id: UUID) -> None:
    query = update.callback_query

    async with session_scope() as session:
        request = await session.get(PurchaseRequest, request_id)

        if not request:
            if query:
                await query.answer("Request not found", show_alert=True)
            return

        buyer = await get_buyer_profile(session, request.buyer_id)
        buyer_user = await session.get(User, request.buyer_id)

        caption = "\n".join(
            [
                "Request Details",
                "",
                f"Request: {request.request_number}",
                f"Status: {request.status.value}",
                f"Quantity: {request.quantity}",
                f"Buyer: {buyer.full_name if buyer else 'Unknown'}",
                f"Buyer Username: @{buyer_user.username}" if buyer_user and buyer_user.username else "Buyer Username: -",
                f"Buyer Phone: {buyer.phone_number if buyer else '-'}",
                f"Buyer Telegram ID: {buyer_user.telegram_id if buyer_user else '-'}",
                "",
                "Description:",
                request.description or "",
            ]
        )

        rows = []

        if request.status == RequestStatus.PENDING_ADMIN_APPROVAL:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Approve",
                        callback_data=f"request:approve:{request.id}",
                    ),
                    InlineKeyboardButton(
                        "Decline",
                        callback_data=f"request:decline:{request.id}",
                    ),
                ]
            )

        if request.status == RequestStatus.ADMIN_SETTLEMENT:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Pending",
                        callback_data=f"settle:pending:{request.id}",
                    ),
                    InlineKeyboardButton(
                        "Settled",
                        callback_data=f"settle:settled:{request.id}",
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        "Cancel",
                        callback_data=f"settle:canceled:{request.id}",
                    )
                ]
            )

        offers = (
            await session.execute(
                select(SellerOffer)
                .where(SellerOffer.request_id == request.id)
                .order_by(SellerOffer.created_at.asc())
            )
        ).scalars().all()

        rows.append([InlineKeyboardButton("Close", callback_data="ui:close")])

        if query and query.message:
            if len(caption) <= 1024:
                await query.message.reply_photo(
                    photo=request.image_file_id,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            else:
                await query.message.reply_photo(
                    photo=request.image_file_id,
                    caption=caption[:1021] + "...",
                    reply_markup=InlineKeyboardMarkup(rows),
                )
                await query.message.reply_text(caption)

            if offers:
                offer_lines = ["Offers:"]

                for offer in offers:
                    offer_seller = await get_seller_profile(session, offer.seller_id)

                    seller_label = (
                        offer_seller.business_name
                        if offer_seller and offer_seller.business_name
                        else str(offer.seller_id)
                    )

                    offer_lines.append(
                        f"• {offer.price:,.2f} {offer.currency} | "
                        f"{offer.status.value} | {seller_label}"
                    )

                await query.message.reply_text("\n".join(offer_lines))

async def _render_seller_app_detail(update: Update, target_user_id: UUID) -> None:
    query = update.callback_query

    async with session_scope() as session:
        seller = await get_seller_profile(session, target_user_id)

        if not seller:
            if query:
                await query.answer("Seller application not found", show_alert=True)
            return

        user = await session.get(User, target_user_id)

        text = "\n".join(
            [
                "Seller Application",
                "",
                f"Name: {seller.full_name}",
                f"Phone: {seller.phone_number}",
                f"Username: @{user.username}" if user and user.username else "Username: -",
                f"Telegram ID: {user.telegram_id if user else '-'}",
                "",
                f"Business: {seller.business_name}",
                f"Location: {seller.location}",
                f"Category: {seller.product_category}",
                f"Shop Number: {seller.shop_number}",
                "",
                f"Status: {seller.approval_status.value}",
            ]
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Approve",
                        callback_data=f"seller_app:approve:{target_user_id}",
                    ),
                    InlineKeyboardButton(
                        "Decline",
                        callback_data=f"seller_app:decline:{target_user_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Edit",
                        callback_data=f"seller_app:edit:{target_user_id}",
                    )
                ],
            ]
        )

        if query and query.message:
            await query.message.reply_text(text, reply_markup=markup)


async def _render_ticket_detail(update: Update, ticket_id: UUID) -> None:
    query = update.callback_query

    async with session_scope() as session:
        ticket = await session.get(SupportTicket, ticket_id)

        if not ticket:
            if query:
                await query.answer("Ticket not found", show_alert=True)
            return

        user = await session.get(User, ticket.user_id)

        text = "\n".join(
            [
                "Support Ticket",
                "",
                f"Ticket: {ticket.ticket_number}",
                f"Status: {ticket.status.value}",
                f"User Telegram ID: {user.telegram_id if user else '-'}",
                f"Username: @{user.username}" if user and user.username else "Username: -",
                "",
                "Problem:",
                ticket.description,
            ]
        )

        if ticket.admin_response:
            text += f"\n\nAdmin Response:\n{ticket.admin_response}"

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Respond",
                        callback_data=f"support:respond:{ticket.id}",
                    ),
                    InlineKeyboardButton(
                        "Close Ticket",
                        callback_data=f"support:close:{ticket.id}",
                    ),
                ]
            ]
        )
        
        if query and query.message:
            await query.message.reply_text(text, reply_markup=markup)