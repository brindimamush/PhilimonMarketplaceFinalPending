# app/bot/middleware.py
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from app.db.session import session_scope
from app.i18n import get_text
from app.models import UserStatus
from app.services.conversation_service import get_session
from app.services.user_service import get_user_by_telegram_id


def _is_support_update(update: Update, state: dict | None) -> bool:
    """
    Suspended users must still have access to support.
    """
    if update.effective_message and update.effective_message.text:
        if update.effective_message.text.strip().startswith("/support"):
            return True

    if update.callback_query and update.callback_query.data:
        if update.callback_query.data.startswith("support:"):
            return True

    if state and state.get("flow") == "support":
        return True

    return False


def require_active_user(handler):
    """
    Blocks suspended users from marketplace actions.

    Spec requirement:
    This check must be centralized, not duplicated across handlers.

    Suspended users can still:
    - start support
    - continue an active support workflow
    """
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg_user = update.effective_user
        if not tg_user:
            return await handler(update, context)

        redis = context.application.bot_data.get("state")
        state = None
        if redis:
            state = await redis.get(tg_user.id)

        if _is_support_update(update, state):
            return await handler(update, context)

        async with session_scope() as session:
            user = await get_user_by_telegram_id(session, tg_user.id)

            if user and user.status == UserStatus.SUSPENDED:
                # If the user is already inside a PostgreSQL-backed support workflow,
                # allow the interaction to continue.
                support_session = await get_session(session, user.id, "SUPPORT_TICKET")
                if _is_suspended_support_allowed(update, state, support_session):
                    return await handler(update, context)

                text = get_text(user.language, "suspended.message")

                if update.callback_query:
                    try:
                        await update.callback_query.answer(text, show_alert=True)
                    except Exception:
                        pass

                    if update.callback_query.message:
                        try:
                            await update.callback_query.message.reply_text(text)
                        except Exception:
                            pass
                elif update.effective_message:
                    await update.effective_message.reply_text(text)

                return

        return await handler(update, context)

    return wrapper

def _is_suspended_support_allowed(update: Update, state: dict | None, support_session) -> bool:
    """
    Suspended users may only use support-related interactions.
    They must not be able to use marketplace commands just because
    they have an active support session.
    """
    if _is_support_update(update, state):
        return True

    # Allow plain-text continuation of an active support workflow.
    # Example: suspended user typed /support and is now describing the issue.
    if support_session and update.effective_message and update.effective_message.text:
        return not update.effective_message.text.strip().startswith("/")

    return False