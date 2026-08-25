# app/services/system_service.py
# LAYER: Application / Services
# PURPOSE: Handles the Transactional Outbox and system-wide notifications.
# WHY HERE: Centralizes the mechanism for deferring external API calls.

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models import OutboxEvent

settings = get_settings()

async def enqueue_outbox(session: AsyncSession, event_type: str, payload: dict) -> None:
    """Adds an event to the Outbox table to be processed asynchronously by the worker."""
    session.add(OutboxEvent(event_type=event_type, payload=payload))

def notification_payload(telegram_id: int, text: str, photo_file_id: str | None = None, buttons: list | None = None) -> dict:
    """Formats the JSON payload expected by the Telegram background worker."""
    payload = {"telegram_id": telegram_id, "text": text}
    if photo_file_id: payload["photo_file_id"] = photo_file_id
    if buttons: payload["buttons"] = buttons
    return payload

async def notify_admins(session: AsyncSession, text: str, photo_file_id: str | None = None, buttons: list | None = None) -> None:
    """Enqueues notifications for all configured admin IDs."""
    for admin_id in settings.admin_ids:
        await enqueue_outbox(session, "notification.telegram", notification_payload(admin_id, text, photo_file_id, buttons))