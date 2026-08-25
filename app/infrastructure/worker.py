# app/infrastructure/worker.py
# LAYER: Infrastructure / Background Processing
# PURPOSE: Background worker using ARQ to process outbox events (broadcasts, notifications).
# WHY HERE: Decouples heavy/external tasks from the main bot thread. 
# If Telegram API is slow, the bot doesn't freeze. It also handles retries automatically.

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import structlog
from arq import cron, run_worker
from arq.connections import RedisSettings
from sqlalchemy import select, update
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter

from app.config.settings import get_settings
from app.core.utils import backoff_seconds, utcnow
from app.db.session import SessionLocal, session_scope
from app.models import (
    BroadcastJob,
    BroadcastJobStatus,
    NotificationDelivery,
    NotificationStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.services.marketplace_service import start_broadcast

settings = get_settings()
logger = structlog.get_logger()
bot = Bot(token=settings.telegram_bot_token)
MAX_ATTEMPTS = 5




def mark_notification_retry(
    delivery: NotificationDelivery,
    *,
    retry_after: int | None = None,
    error: str | None = None,
) -> None:
    """
    Retry/backoff logic for failed Telegram notifications.
    """
    delivery.attempt_count += 1
    delivery.status = (
        NotificationStatus.DEAD_LETTER
        if delivery.attempt_count >= MAX_ATTEMPTS
        else NotificationStatus.FAILED
    )

    delay = retry_after if retry_after is not None else backoff_seconds(delivery.attempt_count)
    delivery.next_retry_at = utcnow() + timedelta(seconds=delay)
    delivery.last_error = error


async def process_notification_delivery(delivery_id: UUID) -> None:
    """
    Sends one notification delivery and updates broadcast job counters.
    """
    async with session_scope() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)

        if not delivery or delivery.status not in (
            NotificationStatus.PENDING,
            NotificationStatus.FAILED,
        ):
            return

        delivery.status = NotificationStatus.PROCESSING
        await session.flush()

        try:
            await send_payload(delivery.payload)
            delivery.status = NotificationStatus.SENT

            if delivery.broadcast_job_id:
                await session.execute(
                    update(BroadcastJob)
                    .where(BroadcastJob.id == delivery.broadcast_job_id)
                    .values(sent_count=BroadcastJob.sent_count + 1)
                )

        except RetryAfter as e:
            mark_notification_retry(delivery, retry_after=int(e.retry_after), error="RetryAfter")

        except Exception as e:
            mark_notification_retry(delivery, error=str(e))

        if delivery.status == NotificationStatus.DEAD_LETTER and delivery.broadcast_job_id:
            await session.execute(
                update(BroadcastJob)
                .where(BroadcastJob.id == delivery.broadcast_job_id)
                .values(failed_count=BroadcastJob.failed_count + 1)
            )

        if delivery.broadcast_job_id:
            job = await session.get(BroadcastJob, delivery.broadcast_job_id)
            if job and job.total_recipients <= job.sent_count + job.failed_count:
                job.status = BroadcastJobStatus.COMPLETED


async def poll_notifications(ctx) -> None:
    """
    Polls notification_deliveries and sends due messages.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(NotificationDelivery.id)
            .where(NotificationDelivery.status.in_([NotificationStatus.PENDING, NotificationStatus.FAILED]))
            .where(NotificationDelivery.next_retry_at <= utcnow())
            .order_by(NotificationDelivery.created_at.asc())
            .limit(20)
        )
        ids = result.scalars().all()

    for delivery_id in ids:
        await process_notification_delivery(delivery_id)

async def send_payload(payload: dict) -> None:
    """Sends the actual Telegram message or photo using the Bot API."""
    chat_id = payload.get("telegram_id")
    text = payload.get("text", "")
    photo_file_id = payload.get("photo_file_id")
    buttons = payload.get("buttons")
    
    # Reconstruct InlineKeyboardMarkup from the JSON payload
    keyboard = None
    if buttons:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text=item["text"], callback_data=item["callback_data"]) for item in row] for row in buttons])
        
    if photo_file_id:
        await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=text[:1024], reply_markup=keyboard)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)

def mark_outbox_retry(event: OutboxEvent, *, retry_after: int | None = None, error: str | None = None) -> None:
    """Handles retry logic and exponential backoff for failed outbox events."""
    event.attempt_count += 1
    event.status = OutboxStatus.DEAD_LETTER if event.attempt_count >= MAX_ATTEMPTS else OutboxStatus.FAILED
    delay = retry_after if retry_after is not None else backoff_seconds(event.attempt_count)
    event.next_retry_at = utcnow() + timedelta(seconds=delay)
    event.last_error = error

async def process_outbox_event(event_id: UUID) -> None:
    """Processes a single outbox event (either a broadcast trigger or a direct notification)."""
    async with session_scope() as session:
        event = await session.get(OutboxEvent, event_id)
        if not event or event.status not in (OutboxStatus.PENDING, OutboxStatus.FAILED): return
        
        event.status = OutboxStatus.PROCESSING
        await session.flush()
        try:
            if event.event_type == "request.broadcast":
                # Calls the service layer to generate individual notification records
                await start_broadcast(session, UUID(event.payload["request_id"]))
            elif event.event_type == "notification.telegram":
                # Sends the message directly via Telegram API
                await send_payload(event.payload)
            event.status = OutboxStatus.DONE
        except RetryAfter as e:
            mark_outbox_retry(event, retry_after=int(e.retry_after), error="RetryAfter")
        except Exception as e:
            mark_outbox_retry(event, error=str(e))

async def poll_outbox(ctx) -> None:
    """Cron job: Fetches pending/failed outbox events and processes them."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(OutboxEvent.id).where(OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.FAILED]))
            .where(OutboxEvent.next_retry_at <= utcnow()).order_by(OutboxEvent.created_at.asc()).limit(10)
        )
        ids = result.scalars().all()
    for event_id in ids:
        await process_outbox_event(event_id)

class WorkerSettings:
    functions = [poll_outbox, poll_notifications]
    cron_jobs = [
        cron(poll_outbox, second={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(poll_notifications, second={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 60
    max_jobs = 10

def main() -> None:
    run_worker(WorkerSettings)