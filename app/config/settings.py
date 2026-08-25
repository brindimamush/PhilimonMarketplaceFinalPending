# app/config/settings.py
from functools import lru_cache

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

    # Marketplace limits required by the production spec
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
        return [int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip()]

    @property
    def allowed_image_mime_types_set(self) -> set[str]:
        return {item.strip() for item in self.allowed_image_mime_types.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()