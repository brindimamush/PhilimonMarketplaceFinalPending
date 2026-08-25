# app/bot/buyer_registration_flow.py

import structlog
from uuid import uuid4

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
from app.services.conversation_service import (
    clear_workflow,
    get_session,
    start_workflow,
    update_workflow,
)
from app.services.user_service import (
    get_buyer_profile,
    get_or_create_user,
    register_buyer,
)

logger = structlog.get_logger()

WORKFLOW = "BUYER_REGISTRATION"


async def start_buyer_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for buyer registration.

    Spec:
    - Show buyer rules.
    - Require agreement.
    - Use Telegram native contact sharing.
    - Ask full name.
    - Create buyer profile idempotently.
    """
    tg_user = update.effective_user
    message = update.effective_message

    if not tg_user or not message:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        buyer = await get_buyer_profile(session, user.id)
        if buyer:
            await message.reply_text(get_text(lang, "registration.buyer_complete"))
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
                    get_text(lang, "buttons.i_agree"),
                    callback_data="buyer_rules:agree",
                ),
                InlineKeyboardButton(
                    get_text(lang, "seller.rules.decline"),
                    callback_data="buyer_rules:decline",
                ),
            ]
        ]
    )

    await message.reply_text(
        get_text(lang, "rules.buyer"),
        reply_markup=markup,
    )


async def handle_buyer_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles Telegram contact sharing for buyer registration.

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

        await update_workflow(
            session,
            user.id,
            WORKFLOW,
            "full_name",
            {"phone_number": normalized_phone},
        )

        await message.reply_text(get_text(lang, "prompt.full_name"))

    return True


async def handle_buyer_registration_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles text input for buyer registration.

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

        if record.state == "full_name":
            full_name = " ".join(text.split())

            if len(full_name) < 3:
                await message.reply_text(get_text(lang, "error.invalid_full_name"))
                return True

            updated = await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "confirm",
                {"full_name": full_name},
            )

            payload = updated.payload or {}

            summary = "\n".join(
                [
                    get_text(lang, "buttons.register_buyer"),
                    "",
                    f"{get_text(lang, 'seller.label_full_name')}: {payload.get('full_name', '')}",
                    f"{get_text(lang, 'seller.label_phone')}: {payload.get('phone_number', '')}",
                ]
            )

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            get_text(lang, "buttons.submit"),
                            callback_data="buyer_confirm:submit",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "buttons.edit"),
                            callback_data="buyer_confirm:edit",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "buttons.cancel"),
                            callback_data="buyer_confirm:cancel",
                        ),
                    ]
                ]
            )

            await message.reply_text(summary, reply_markup=markup)
            return True

    return False


async def handle_buyer_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles buyer registration callbacks.

    Returns True if this handler consumed the callback.
    """
    query = update.callback_query
    tg_user = update.effective_user

    if not query or not tg_user or not query.data:
        return False

    data = query.data

    if not (
        data.startswith("buyer_rules:")
        or data.startswith("buyer_confirm:")
    ):
        return False

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = supported_language(user.language)

        # ------------------------------------------------------------
        # Buyer rules
        # ------------------------------------------------------------
        if data.startswith("buyer_rules:"):
            action = data.split(":")[-1]

            if action == "agree":
                buyer = await get_buyer_profile(session, user.id)
                if buyer:
                    await clear_workflow(session, user.id, WORKFLOW)
                    await query.answer()
                    await query.message.reply_text(
                        get_text(lang, "registration.buyer_complete")
                    )
                    return True

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
        # Buyer confirmation
        # ------------------------------------------------------------
        if data.startswith("buyer_confirm:"):
            record = await get_session(session, user.id, WORKFLOW)

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
                    "full_name",
                    {},
                )

                await query.answer()

                if query.message:
                    try:
                        await query.edit_message_reply_markup(None)
                    except Exception:
                        pass

                await query.message.reply_text(get_text(lang, "prompt.full_name"))
                return True

            if action == "submit":
                missing = [
                    key
                    for key in ("phone_number", "full_name", "draft_id")
                    if not payload.get(key)
                ]

                if missing:
                    await query.answer(
                        get_text(lang, "error.request_incomplete"),
                        show_alert=True,
                    )
                    return True

                try:
                    async def _create():
                        profile = await register_buyer(
                            session,
                            tg_user,
                            payload["phone_number"],
                            payload["full_name"],
                        )
                        return {"buyer_profile_id": str(profile.id)}

                    await execute_idempotent(
                        session,
                        f"register_buyer:{user.id}:{payload['draft_id']}",
                        "BUYER_REGISTER",
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
                        get_text(lang, "registration.buyer_complete"),
                        reply_markup=ReplyKeyboardRemove(),
                    )

                except DomainError as exc:
                    await query.answer(translate_error(lang, exc), show_alert=True)
                except Exception:
                    logger.exception("buyer_registration_failed")
                    await query.answer(get_text(lang, "error.generic"), show_alert=True)

                return True

            await query.answer(get_text(lang, "error.unknown_action"))
            return True

    return False