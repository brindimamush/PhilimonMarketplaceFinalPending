# app/bot/user_dashboards.py
from uuid import UUID

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.db.session import session_scope
from app.i18n import get_text, status_text, supported_language
from app.models import PurchaseRequest, RequestStatus, SellerOffer
from app.services.marketplace_service import (
    get_buyer_requests_page,
    get_seller_offers_page,
)
from app.services.user_service import get_or_create_user

logger = structlog.get_logger()

PAGE_SIZE = 5


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


async def my_requests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _render_requests(update, context, 1)


async def my_offers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _render_offers(update, context, 1)


async def _render_requests(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    tg_user = update.effective_user
    query = update.callback_query

    if not tg_user:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        page, total_pages, total, requests = await get_buyer_requests_page(
            session,
            user.id,
            page,
            PAGE_SIZE,
        )

        if query:
            await query.answer()

        if not requests:
            await _reply_or_edit(update, get_text(lang, "my_requests.empty"))
            return

        rows = []

        for request in requests:
            label = f"{request.request_number} • {status_text(lang, request.status.value)}"
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"myreq:view:{request.id}",
                    )
                ]
            )

        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    get_text(lang, "buttons.previous"),
                    callback_data=f"myreq:page:{page - 1}",
                )
            )

        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    get_text(lang, "buttons.next"),
                    callback_data=f"myreq:page:{page + 1}",
                )
            )

        if nav:
            rows.append(nav)

        rows.append(
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.close"),
                    callback_data="ui:close",
                )
            ]
        )

        text = get_text(
            lang,
            "my_requests.page",
            page=page,
            total_pages=total_pages,
            total=total,
        )

        await _reply_or_edit(update, text, InlineKeyboardMarkup(rows))


async def _render_offers(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    tg_user = update.effective_user
    query = update.callback_query

    if not tg_user:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        page, total_pages, total, offers = await get_seller_offers_page(
            session,
            user.id,
            page,
            PAGE_SIZE,
        )

        if query:
            await query.answer()

        if not offers:
            await _reply_or_edit(update, get_text(lang, "my_offers.empty"))
            return

        rows = []

        for offer, request in offers:
            label = (
                f"{request.request_number} • {offer.price:,.2f} {offer.currency} • "
                f"{status_text(lang, offer.status.value)}"
            )

            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"myoffer:view:{offer.id}",
                    )
                ]
            )

        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    get_text(lang, "buttons.previous"),
                    callback_data=f"myoffer:page:{page - 1}",
                )
            )

        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    get_text(lang, "buttons.next"),
                    callback_data=f"myoffer:page:{page + 1}",
                )
            )

        if nav:
            rows.append(nav)

        rows.append(
            [
                InlineKeyboardButton(
                    get_text(lang, "buttons.close"),
                    callback_data="ui:close",
                )
            ]
        )

        text = get_text(
            lang,
            "my_offers.page",
            page=page,
            total_pages=total_pages,
            total=total,
        )

        await _reply_or_edit(update, text, InlineKeyboardMarkup(rows))


async def handle_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles dashboard pagination and detail callbacks.

    Returns True if this handler consumed the callback.
    """
    query = update.callback_query
    tg_user = update.effective_user

    if not query or not tg_user:
        return False

    data = query.data or ""

    if data.startswith("myreq:page:"):
        page = int(data.split(":")[-1])
        await _render_requests(update, context, page)
        return True

    if data.startswith("myoffer:page:"):
        page = int(data.split(":")[-1])
        await _render_offers(update, context, page)
        return True

    if data.startswith("myreq:view:"):
        request_id = UUID(data.split(":")[-1])

        async with session_scope() as session:
            user = await get_or_create_user(session, tg_user)
            lang = supported_language(user.language)

            request = await session.get(PurchaseRequest, request_id)

            if not request or request.buyer_id != user.id:
                await query.answer(
                    get_text(lang, "error.request_not_yours"),
                    show_alert=True,
                )
                return True

            caption = "\n".join(
                [
                    get_text(lang, "request.details_title"),
                    "",
                    f"{get_text(lang, 'request.label')}: {request.request_number}",
                    f"{get_text(lang, 'request.status')}: {status_text(lang, request.status.value)}",
                    f"{get_text(lang, 'request.qty_label')}: {request.quantity}",
                    "",
                    f"{get_text(lang, 'request.description')}:",
                    request.description or "",
                ]
            )

            markup = None

            if request.status in (
                RequestStatus.COLLECTING_OFFERS,
                RequestStatus.BUYER_SELECTING,
                RequestStatus.SELLER_SELECTED,
                RequestStatus.ADMIN_SETTLEMENT,
            ):
                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                get_text(lang, "buttons.view_offers"),
                                callback_data=f"request:offers:{request.id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                get_text(lang, "buttons.close"),
                                callback_data="ui:close",
                            )
                        ],
                    ]
                )

            await query.answer()

            if query.message:
                await query.message.reply_photo(
                    photo=request.image_file_id,
                    caption=caption,
                    reply_markup=markup,
                )

        return True

    if data.startswith("myoffer:view:"):
        offer_id = UUID(data.split(":")[-1])

        async with session_scope() as session:
            user = await get_or_create_user(session, tg_user)
            lang = supported_language(user.language)

            offer = await session.get(SellerOffer, offer_id)

            if not offer or offer.seller_id != user.id:
                await query.answer(
                    get_text(lang, "error.offer_not_yours"),
                    show_alert=True,
                )
                return True

            request = await session.get(PurchaseRequest, offer.request_id)

            if not request:
                await query.answer(get_text(lang, "error.request_not_found"), show_alert=True)
                return True

            caption = "\n".join(
                [
                    get_text(lang, "offer.details_title"),
                    "",
                    f"{get_text(lang, 'request.label')}: {request.request_number}",
                    f"{get_text(lang, 'offer.request_status')}: {status_text(lang, request.status.value)}",
                    f"{get_text(lang, 'request.qty_label')}: {request.quantity}",
                    f"{get_text(lang, 'offer.your_price')}: {offer.price:,.2f} {offer.currency}",
                    f"{get_text(lang, 'offer.offer_status')}: {status_text(lang, offer.status.value)}",
                    "",
                    f"{get_text(lang, 'request.description')}:",
                    request.description or "",
                ]
            )

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            get_text(lang, "buttons.close"),
                            callback_data="ui:close",
                        )
                    ]
                ]
            )

            await query.answer()

            if query.message:
                await query.message.reply_photo(
                    photo=request.image_file_id,
                    caption=caption,
                    reply_markup=markup,
                )

        return True

    return False