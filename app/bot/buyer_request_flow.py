# app/bot/buyer_request_flow.py
from io import BytesIO
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config.settings import get_settings
from app.core.exceptions import DomainError, LocalizedDomainError
from app.core.images import validate_image_bytes
from app.core.rate_limiter import check_rate_limit
from app.core.utils import execute_idempotent
from app.db.session import session_scope
from app.i18n import get_text, supported_language, translate_error
from app.models import PurchaseRequest, RequestStatus, UserStatus
from app.services.conversation_service import (
    clear_workflow,
    get_session,
    start_workflow,
    update_workflow,
)
from app.services.marketplace_service import create_purchase_request
from app.services.user_service import get_buyer_profile, get_or_create_user

settings = get_settings()
logger = structlog.get_logger()

WORKFLOW = "BUYER_REQUEST_CREATION"

def _lang(user) -> str:
    return supported_language(user.language if user else None)

def _summary_caption(lang: str, payload: dict) -> str:
    return "\n".join(
        [
            get_text(lang, "request.summary_title"),
            "",
            f"{get_text(lang, 'request.description')}:",
            payload.get("description", ""),
            "",
            f"{get_text(lang, 'request.qty_label')}: {payload.get('quantity', '')}",
        ]
    )

async def new_request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Starts the buyer purchase request workflow.
    Workflow:
    image -> description -> quantity -> confirmation -> submit
    """
    tg_user = update.effective_user
    message = update.effective_message
    if not tg_user or not message:
        return
        
    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = _lang(user)
        
        if user.status == UserStatus.SUSPENDED:
            await message.reply_text(get_text(lang, "suspended.message"))
            return
            
        buyer = await get_buyer_profile(session, user.id)
        if not buyer:
            await message.reply_text(get_text(lang, "error.register_buyer_first"))
            return
            
        # ======================================================================
        # RATE LIMITING: Request Creation
        # Spec: Protect the bot against accidental loops and abuse.
        # Limit: 3 new request initiations per minute per user.
        # ======================================================================
        redis = context.application.bot_data.get("state").redis
        try:
            await check_rate_limit(redis, tg_user.id, "create_request", limit=3, window_seconds=60)
        except LocalizedDomainError as exc:
            await message.reply_text(translate_error(lang, exc))
            return
        # ======================================================================
            
        pending_count = await session.scalar(
            select(func.count())
            .select_from(PurchaseRequest)
            .where(
                PurchaseRequest.buyer_id == user.id,
                PurchaseRequest.status == RequestStatus.PENDING_ADMIN_APPROVAL,
            )
        ) or 0
        
        if pending_count >= settings.max_pending_buyer_requests:
            await message.reply_text(
                get_text(
                    lang,
                    "error.max_pending_requests",
                    max=settings.max_pending_buyer_requests,
                )
            )
            return
            
        await start_workflow(
            session,
            user.id,
            WORKFLOW,
            "image",
            {"draft_id": uuid4().hex},
        )
        
    await message.reply_text(get_text(lang, "prompt.send_item_image"))

async def new_request_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Receives the buyer request image.
    Security:
    - Do not trust filename or extension.
    - Validate size.
    - Validate decodability.
    - Validate MIME type.
    """
    message = update.effective_message
    tg_user = update.effective_user
    if not message or not tg_user or not message.photo:
        return

    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = _lang(user)

        # ======================================================================
        # RATE LIMITING: Image Upload
        # Spec: Rate limit image uploads to prevent storage/API abuse.
        # Limit: 10 image uploads per minute per user.
        # ======================================================================
        redis = context.application.bot_data.get("state").redis
        try:
            await check_rate_limit(redis, tg_user.id, "upload_image", limit=10, window_seconds=60)
        except LocalizedDomainError as exc:
            await message.reply_text(translate_error(lang, exc))
            return
        # ======================================================================

        record = await get_session(session, user.id, WORKFLOW)
        
        if not record or record.state != "image":
            await message.reply_text(get_text(lang, "error.unexpected_image"))
            return
            
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > settings.max_image_bytes:
            await message.reply_text(get_text(lang, "error.unexpected_image"))
            return
            
        try:
            tg_file = await context.bot.get_file(photo.file_id)
            buffer = BytesIO()
            # python-telegram-bot v22 requires an output buffer.
            downloaded = await tg_file.download_to_memory(out=buffer)
            if isinstance(downloaded, memoryview):
                data = downloaded.tobytes()
            elif isinstance(downloaded, (bytes, bytearray)):
                data = bytes(downloaded)
            else:
                data = buffer.getvalue()
                
            validate_image_bytes(
                data,
                max_bytes=settings.max_image_bytes,
                allowed_mimes=settings.allowed_image_mime_types_set,
            )
        except DomainError as exc:
            await message.reply_text(translate_error(lang, exc))
            return
        except Exception:
            logger.exception("buyer_request_image_download_failed")
            await message.reply_text(get_text(lang, "error.generic"))
            return
            
        await update_workflow(
            session,
            user.id,
            WORKFLOW,
            "description",
            {
                "image_file_id": photo.file_id,
                "image_unique_id": photo.file_unique_id,
            },
        )
        
    await message.reply_text(get_text(lang, "prompt.send_description"))

async def handle_request_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles text steps for buyer request creation.
    Returns True if this handler consumed the update.
    """
    message = update.effective_message
    tg_user = update.effective_user
    if not message or not tg_user or not message.text:
        return False
        
    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = _lang(user)
        record = await get_session(session, user.id, WORKFLOW)
        
        if not record:
            return False
            
        text = message.text.strip()
        
        if record.state == "description":
            if len(text) < settings.min_description_length:
                await message.reply_text(get_text(lang, "error.invalid_description"))
                return True
            if len(text) > settings.max_text_length:
                text = text[: settings.max_text_length]
                
            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "quantity",
                {"description": text},
            )
            await message.reply_text(get_text(lang, "prompt.send_quantity"))
            return True
            
        if record.state == "quantity":
            try:
                quantity = int(text)
            except (TypeError, ValueError):
                await message.reply_text(get_text(lang, "error.invalid_quantity"))
                return True
            if quantity < 1:
                await message.reply_text(get_text(lang, "error.invalid_quantity"))
                return True
                
            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "confirm",
                {"quantity": quantity},
            )
            payload = {**(record.payload or {}), "quantity": quantity}
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            get_text(lang, "buttons.confirm"),
                            callback_data="draft:confirm",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "buttons.edit"),
                            callback_data="draft:edit",
                        ),
                        InlineKeyboardButton(
                            get_text(lang, "buttons.cancel"),
                            callback_data="draft:cancel",
                        ),
                    ]
                ]
            )
            await message.reply_photo(
                photo=payload.get("image_file_id"),
                caption=_summary_caption(lang, payload),
                reply_markup=markup,
            )
            return True
            
        if record.state == "confirm":
            await message.reply_text(get_text(lang, "error.unexpected_state"))
            return True
            
    return False

async def handle_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles buyer request draft confirmation callbacks.
    Returns True if this handler consumed the callback.
    """
    query = update.callback_query
    tg_user = update.effective_user
    if not query or not tg_user:
        return False
        
    data = query.data or ""
    if not data.startswith("draft:"):
        return False
        
    action = data.split(":", 1)[1]
    
    async with session_scope() as session:
        user = await get_or_create_user(session, tg_user)
        lang = _lang(user)
        record = await get_session(session, user.id, WORKFLOW)
        
        if not record or record.state != "confirm":
            await query.answer(get_text(lang, "error.unknown_action"))
            return True
            
        payload = record.payload or {}
        
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
                    reply_markup=None,
                )
            return True
            
        if action == "edit":
            await update_workflow(
                session,
                user.id,
                WORKFLOW,
                "image",
                {
                    "description": "",
                    "quantity": 0,
                },
            )
            await query.answer()
            if query.message:
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass
                await query.message.reply_text(
                    get_text(lang, "prompt.send_item_image"),
                    reply_markup=None,
                )
            return True
            
        if action == "confirm":
            missing = [
                key
                for key in ("image_file_id", "description", "quantity", "draft_id")
                if payload.get(key) in (None, "")
            ]
            if missing:
                await query.answer(
                    get_text(lang, "error.request_incomplete"),
                    show_alert=True,
                )
                return True
                
            try:
                async def _create():
                    request = await create_purchase_request(
                        session,
                        tg_user,
                        payload["image_file_id"],
                        payload.get("image_unique_id"),
                        payload["quantity"],
                        payload["description"],
                    )
                    return {"request_number": request.request_number}
                    
                result = await execute_idempotent(
                    session,
                    f"create_purchase_request:{user.id}:{payload['draft_id']}",
                    "REQUEST_CREATE",
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
                        get_text(
                            lang,
                            "request.submitted",
                            request_number=result.get("request_number", ""),
                        )
                    )
            except DomainError as exc:
                await query.answer(translate_error(lang, exc), show_alert=True)
            except Exception:
                logger.exception("buyer_request_confirm_failed")
                await query.answer(get_text(lang, "error.generic"), show_alert=True)
                
            return True
            
    return True