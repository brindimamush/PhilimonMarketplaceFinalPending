# app/bot/application.py

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


def build_application(redis_state: RedisConversationState) -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["state"] = redis_state

    # Core commands
    application.add_handler(CommandHandler("start", require_active_user(start_cmd)))
    application.add_handler(CommandHandler("menu", require_active_user(start_cmd)))

    # Buyer commands
    application.add_handler(CommandHandler("newrequest", require_active_user(new_request_cmd)))
    application.add_handler(CommandHandler("myrequests", require_active_user(my_requests_cmd)))

    # Seller commands
    application.add_handler(CommandHandler("myoffers", require_active_user(my_offers_cmd)))
    application.add_handler(
        CommandHandler("registerseller", require_active_user(start_seller_registration))
    )
    # Support and language
    application.add_handler(CommandHandler("support", require_active_user(support_cmd)))
    application.add_handler(CommandHandler("language", require_active_user(language_cmd)))

    # Admin commands
    application.add_handler(CommandHandler("admin", require_active_user(admin_cmd)))
    application.add_handler(CommandHandler("search", require_active_user(search_cmd)))

    # Contact sharing must be routed because both buyer and seller registration use it.
    application.add_handler(
        MessageHandler(filters.CONTACT, require_active_user(contact_router))
    )

    # Buyer request image
    application.add_handler(
        MessageHandler(filters.PHOTO, require_active_user(new_request_photo))
    )

    # Text router
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, require_active_user(text_router))
    )

    # Callback router
    application.add_handler(CallbackQueryHandler(require_active_user(callback_router)))

    return application