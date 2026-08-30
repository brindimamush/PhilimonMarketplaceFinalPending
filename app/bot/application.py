# app/bot/application.py

from typing import Callable, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.admin_flow import admin_cmd, search_cmd
from app.bot.buyer_request_flow import (
    new_request_cmd,
    new_request_photo,
)
from app.bot.correlation_middleware import log_correlation
from app.bot.handlers import (
    callback_router,
    contact_router,
    language_cmd,
    start_cmd,
    support_cmd,
    text_router,
)
from app.bot.middleware import require_active_user
from app.bot.seller_registration_flow import start_seller_registration
from app.bot.user_dashboards import my_offers_cmd, my_requests_cmd
from app.config.settings import get_settings
from app.infrastructure.redis import RedisConversationState

settings = get_settings()


def build_application(
    redis_state: RedisConversationState,
    post_init: Optional[Callable] = None,
) -> Application:
    builder = Application.builder().token(settings.telegram_bot_token)

    if post_init is not None:
        builder.post_init(post_init)

    application = builder.build()
    application.bot_data["state"] = redis_state

    # Core commands
    application.add_handler(
        CommandHandler("start", log_correlation(require_active_user(start_cmd)))
    )
    application.add_handler(
        CommandHandler("menu", log_correlation(require_active_user(start_cmd)))
    )

    # Buyer commands
    application.add_handler(
        CommandHandler("newrequest", log_correlation(require_active_user(new_request_cmd)))
    )
    application.add_handler(
        CommandHandler("myrequests", log_correlation(require_active_user(my_requests_cmd)))
    )

    # Seller commands
    application.add_handler(
        CommandHandler("myoffers", log_correlation(require_active_user(my_offers_cmd)))
    )
    application.add_handler(
        CommandHandler("registerseller", log_correlation(require_active_user(start_seller_registration)))
    )

    # Support and language
    application.add_handler(
        CommandHandler("support", log_correlation(require_active_user(support_cmd)))
    )
    application.add_handler(
        CommandHandler("language", log_correlation(require_active_user(language_cmd)))
    )

    # Admin commands
    application.add_handler(
        CommandHandler("admin", log_correlation(require_active_user(admin_cmd)))
    )
    application.add_handler(
        CommandHandler("search", log_correlation(require_active_user(search_cmd)))
    )

    # Contact sharing must be routed because both buyer and seller registration use it.
    application.add_handler(
        MessageHandler(filters.CONTACT, log_correlation(require_active_user(contact_router)))
    )

    # Buyer request image
    application.add_handler(
        MessageHandler(filters.PHOTO, log_correlation(require_active_user(new_request_photo)))
    )

    # Text router
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, log_correlation(require_active_user(text_router)))
    )

    # Callback router
    application.add_handler(
        CallbackQueryHandler(log_correlation(require_active_user(callback_router)))
    )

    return application