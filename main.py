# main.py

import structlog
from telegram import Update

from app.bot.application import build_application
from app.config.settings import get_settings
from app.infrastructure.logging import configure_logging
from app.infrastructure.redis import RedisConversationState

logger = structlog.get_logger()


async def post_init(application) -> None:
    """
    Runs after the application starts.

    In polling mode:
    - remove any webhook so long polling works cleanly

    In webhook mode:
    - the actual webhook registration is handled by run_webhook(...)
    """
    settings = get_settings()

    if not settings.is_webhook:
        await application.bot.delete_webhook(
            drop_pending_updates=settings.drop_pending_updates
        )
        logger.info("long_polling_started")
    else:
        logger.info(
            "webhook_mode_enabled",
            public_url=settings.public_webhook_url,
            listen_host=settings.webhook_listen_host,
            listen_port=settings.webhook_listen_port,
            url_path=settings.webhook_url_path,
        )


def main() -> None:
    configure_logging()

    settings = get_settings()
    redis_state = RedisConversationState(settings.redis_url)

    app = build_application(redis_state, post_init=post_init)

    if settings.is_webhook:
        if not settings.public_webhook_url:
            raise SystemExit(
                "PUBLIC_WEBHOOK_URL is required when BOT_MODE=webhook"
            )

        app.run_webhook(
            allowed_updates=Update.ALL_TYPES,
            listen_host=settings.webhook_listen_host,
            listen_port=settings.webhook_listen_port,
            url_path=settings.webhook_url_path,
            cert_path=settings.webhook_cert_path or None,
            key_path=settings.webhook_key_path or None,
            webhook_url=settings.public_webhook_url,
            secret_token=settings.effective_webhook_secret_token,
            drop_pending_updates=settings.drop_pending_updates,
        )
    else:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=settings.drop_pending_updates,
        )


if __name__ == "__main__":
    main()