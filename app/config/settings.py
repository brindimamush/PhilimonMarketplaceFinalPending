# app/config/settings.py

from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str

    redis_url: str = "redis://redis:6379/0"

    postgres_user: str = "marketplace"
    postgres_password: str = "marketplace"
    postgres_db: str = "marketplace"
    postgres_host: str = "db"
    postgres_port: int = 5432

    admin_telegram_ids: str = ""
    default_language: str = "en"

    # ------------------------------------------------------------------
    # Bot transport mode
    # ------------------------------------------------------------------
    # "polling" or "webhook"
    bot_mode: str = "polling"

    # Webhook settings
    public_webhook_url: str = ""
    webhook_path: str = ""
    webhook_listen_host: str = "0.0.0.0"
    webhook_listen_port: int = 8443
    webhook_secret_token: str = ""
    webhook_cert_path: str = ""
    webhook_key_path: str = ""

    # Applies to both polling and webhook startup
    drop_pending_updates: bool = False

    # ------------------------------------------------------------------
    # Marketplace limits required by the production spec
    # ------------------------------------------------------------------
    max_pending_buyer_requests: int = 3
    max_accepted_sellers: int = 3

    # Validation limits
    decline_reason_min_length: int = 3
    support_min_length: int = 5

    # General text limits
    max_text_length: int = 2000
    min_description_length: int = 5
    min_full_name_length: int = 3
    min_reason_length: int = 3

    # Image upload limits
    max_image_bytes: int = 5 * 1024 * 1024
    allowed_image_mime_types: str = "image/jpeg,image/png,image/webp"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def admin_ids(self) -> list[int]:
        return [
            int(x.strip())
            for x in self.admin_telegram_ids.split(",")
            if x.strip()
        ]

    @property
    def allowed_image_mime_types_set(self) -> set[str]:
        return {
            item.strip()
            for item in self.allowed_image_mime_types.split(",")
            if item.strip()
        }

    @property
    def is_webhook(self) -> bool:
        return self.bot_mode.strip().lower() == "webhook"

    @property
    def webhook_url_path(self) -> str:
        """
        Local URL path used by the webhook server.

        Priority:
        1. WEBHOOK_PATH
        2. path component of PUBLIC_WEBHOOK_URL
        3. fallback to "webhook"
        """
        if self.webhook_path:
            return self.webhook_path.strip("/")

        parsed = urlparse(self.public_webhook_url)
        path = (parsed.path or "").strip("/")
        return path or "webhook"

    @property
    def effective_webhook_secret_token(self) -> str:
        """
        Secret token used to validate incoming webhook calls.

        If not explicitly configured, fall back to the bot token.
        For production, set WEBHOOK_SECRET_TOKEN explicitly.
        """
        return self.webhook_secret_token or self.telegram_bot_token


@lru_cache
def get_settings() -> Settings:
    return Settings()