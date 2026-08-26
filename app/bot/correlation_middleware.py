# app/bot/correlation_middleware.py
from functools import wraps

import structlog
from telegram import Update
from telegram.ext import ContextTypes


def log_correlation(handler):
    """
    Injects the Telegram update_id into the structlog context.
    WHY: Spec requires correlating logs with specific Telegram updates for production debugging.
    """
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update and update.update_id:
            structlog.contextvars.bind_contextvars(update_id=update.update_id)
        try:
            return await handler(update, context)
        finally:
            structlog.contextvars.clear_contextvars()
    return wrapper