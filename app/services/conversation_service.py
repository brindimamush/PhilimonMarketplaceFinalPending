# app/services/conversation_service.py
# LAYER: Application / Services
# PURPOSE: PostgreSQL-backed workflow state.

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utcnow
from app.models import ConversationSession


async def get_session(
    session: AsyncSession,
    user_id,
    workflow: str,
) -> ConversationSession | None:
    return await session.scalar(
        select(ConversationSession).where(
            ConversationSession.user_id == user_id,
            ConversationSession.workflow == workflow,
        )
    )


async def start_workflow(
    session: AsyncSession,
    user_id,
    workflow: str,
    state: str,
    payload: dict | None = None,
) -> ConversationSession:
    """
    Starts or restarts a workflow.

    This should replace Redis for critical workflows.
    """
    existing = await session.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == user_id,
            ConversationSession.workflow == workflow,
        )
        .with_for_update()
    )

    if existing:
        existing.state = state
        existing.payload = payload or {}
        existing.version += 1
        existing.expires_at = utcnow() + timedelta(days=7)
        return existing

    record = ConversationSession(
        user_id=user_id,
        workflow=workflow,
        state=state,
        payload=payload or {},
        version=1,
        expires_at=utcnow() + timedelta(days=7),
    )
    session.add(record)
    await session.flush()
    return record


async def update_workflow(
    session: AsyncSession,
    user_id,
    workflow: str,
    state: str,
    payload_updates: dict | None = None,
) -> ConversationSession:
    record = await session.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == user_id,
            ConversationSession.workflow == workflow,
        )
        .with_for_update()
    )

    if not record:
        return await start_workflow(session, user_id, workflow, state, payload_updates)

    record.state = state
    record.payload = {**(record.payload or {}), **(payload_updates or {})}
    record.version += 1
    record.expires_at = utcnow() + timedelta(days=7)

    return record


async def clear_workflow(
    session: AsyncSession,
    user_id,
    workflow: str,
) -> None:
    record = await session.scalar(
        select(ConversationSession).where(
            ConversationSession.user_id == user_id,
            ConversationSession.workflow == workflow,
        )
    )

    if record:
        await session.delete(record)