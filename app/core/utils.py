# app/core/utils.py
# LAYER: Domain / Core
# PURPOSE: Pure utility functions, idempotency wrappers, and audit logging.

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import phonenumbers
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import LocalizedDomainError
from app.models.system import AuditLog, IdempotencyRecord, IdempotencyStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_ethiopian_phone(raw: str) -> str:
    """
    Validates and normalizes Ethiopian phone numbers using libphonenumber.

    Spec requirement:
    - Do not rely solely on regex.
    - Accept +2519XXXXXXXX, 09XXXXXXXX, etc.
    - Store canonical E.164 representation.
    """
    value = (raw or "").strip()

    # Telegram/contact payloads sometimes arrive as 00251...
    if value.startswith("00"):
        value = "+" + value[2:]

    try:
        parsed = phonenumbers.parse(value, "ET")
    except phonenumbers.NumberParseException:
        raise LocalizedDomainError("error.invalid_ethiopian_phone")

    if not phonenumbers.is_valid_number_for_region(parsed, "ET"):
        raise LocalizedDomainError("error.ethiopian_phone")

    if not phonenumbers.is_valid_number(parsed):
        raise LocalizedDomainError("error.invalid_ethiopian_phone")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id=None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
    before: dict | None = None,
    after: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Writes an immutable audit log entry."""
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_data=before,
            after_data=after,
            metadata_=metadata,
        )
    )

async def execute_idempotent(
    session: AsyncSession,
    key: str,
    operation_type: str,
    executor: Callable[[], Awaitable[Any]],
    *,
    request_hash: str | None = None,
    user_id=None,
    request_id=None,
) -> dict:
    """
    Ensures a state-changing operation executes exactly once.

    This protects against duplicate Telegram callbacks and retries.

    Hardening:
    - Uses FOR UPDATE when an idempotency record already exists.
    - Uses a savepoint and unique-constraint fallback when inserting a new record.
    - If a concurrent transaction inserted the same key first, this transaction
      re-reads the winning record instead of failing.
    """
    stmt = (
        select(IdempotencyRecord)
        .where(IdempotencyRecord.idempotency_key == key)
        .with_for_update()
    )

    record = (await session.execute(stmt)).scalar_one_or_none()

    if record:
        if record.status == IdempotencyStatus.COMPLETED:
            return record.response_payload or {}

        if record.status == IdempotencyStatus.PROCESSING:
            return {"status": "processing"}

        record.status = IdempotencyStatus.PROCESSING
        if user_id is not None:
            record.user_id = user_id
        if request_id is not None:
            record.request_id = request_id

        await session.flush()
    else:
        try:
            async with session.begin_nested():
                record = IdempotencyRecord(
                    idempotency_key=key,
                    operation_type=operation_type,
                    request_hash=request_hash,
                    status=IdempotencyStatus.PROCESSING,
                    user_id=user_id,
                    request_id=request_id,
                )
                session.add(record)
                await session.flush()
        except IntegrityError:
            # Another transaction inserted the same idempotency key first.
            # Re-read it and respect its state.
            record = (await session.execute(stmt)).scalar_one_or_none()
            if not record:
                raise

            if record.status == IdempotencyStatus.COMPLETED:
                return record.response_payload or {}

            if record.status == IdempotencyStatus.PROCESSING:
                return {"status": "processing"}

            record.status = IdempotencyStatus.PROCESSING
            if user_id is not None:
                record.user_id = user_id
            if request_id is not None:
                record.request_id = request_id

            await session.flush()

    try:
        result = await executor()
        record.status = IdempotencyStatus.COMPLETED
        record.response_payload = result if isinstance(result, dict) else {"result": str(result)}
        await session.flush()
        return result
    except Exception:
        record.status = IdempotencyStatus.FAILED
        await session.flush()
        raise


def backoff_seconds(attempt_count: int) -> int:
    """
    Exponential/backoff schedule for failed background jobs.
    """
    steps = [0, 30, 60, 120, 300, 900, 1800]
    return steps[min(max(attempt_count, 0), len(steps) - 1)]