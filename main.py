# main.py
from telegram import Update

from app.bot.application import build_application
from app.config.settings import get_settings
from app.infrastructure.logging import configure_logging
from app.infrastructure.redis import RedisConversationState


def main() -> None:
    configure_logging()

    settings = get_settings()
    redis_state = RedisConversationState(settings.redis_url)

    app = build_application(redis_state)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()