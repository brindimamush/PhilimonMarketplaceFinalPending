# app/models/system.py
# LAYER: Domain / Models (System Infrastructure)
# PURPOSE: Defines tables for system reliability: Outbox, Idempotency, and Auditing.
# WHY HERE: These are cross-cutting concerns that don't belong to a specific business domain.

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    BroadcastJobStatus,
    IdempotencyStatus,
    NotificationStatus,
    OutboxStatus,
    SuspensionStatus,
)


def enum_type(enum_cls, name):
    return SAEnum(enum_cls, name=name, values_callable=lambda x: [e.value for e in x])

class AuditLog(Base):
    # IMMUTABLE LOG: Tracks every critical state change for compliance and debugging
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    before_data: Mapped[dict | None] = mapped_column(JSONB)
    after_data: Mapped[dict | None] = mapped_column(JSONB)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class IdempotencyRecord(TimestampMixin, Base):
    # IDEMPOTENCY KEY: Prevents duplicate actions if a user double-clicks a button or a network retries
    __tablename__ = "idempotency_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[IdempotencyStatus] = mapped_column(enum_type(IdempotencyStatus, "idempotency_status"), default=IdempotencyStatus.PROCESSING)
    response_payload: Mapped[dict | None] = mapped_column(JSONB)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_requests.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class OutboxEvent(TimestampMixin, Base):
    # TRANSACTIONAL OUTBOX PATTERN: Stores events to be processed asynchronously.
    # WHY: Ensures that database writes and external notifications (Telegram messages) 
    # never fall out of sync. If the DB commits, the event is guaranteed to be sent eventually.
    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(enum_type(OutboxStatus, "outbox_status"), default=OutboxStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)

class NotificationDelivery(TimestampMixin, Base):
    # Tracks individual message deliveries, especially for bulk broadcasts
    __tablename__ = "notification_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(enum_type(NotificationStatus, "notification_status"), default=NotificationStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    broadcast_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("broadcast_jobs.id"))

class BroadcastJob(TimestampMixin, Base):
    __tablename__ = "broadcast_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_requests.id"), unique=True, nullable=False)
    status: Mapped[BroadcastJobStatus] = mapped_column(enum_type(BroadcastJobStatus, "broadcast_job_status"), default=BroadcastJobStatus.PENDING)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)

class UserSuspension(TimestampMixin, Base):
    __tablename__ = "user_suspensions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[SuspensionStatus] = mapped_column(enum_type(SuspensionStatus, "suspension_status"), default=SuspensionStatus.ACTIVE)
    lifted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ConversationSession(TimestampMixin, Base):
    """
    PostgreSQL-backed conversation/workflow state.

    Spec requirement:
    Critical workflow state must not live only in Redis or Python memory.
    """
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "workflow", name="uq_conversation_session_user_workflow"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    workflow: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))