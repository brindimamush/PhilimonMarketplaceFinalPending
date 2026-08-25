# app/models/marketplace.py
# LAYER: Domain / Models (Marketplace Bounded Context)
# PURPOSE: Defines the core transactional entities: Requests, Offers, and Support Tickets.
# WHY HERE: Isolates the complex business logic tables from user identity tables.

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    OfferStatus,
    RequestSellerStatus,
    RequestStatus,
    TicketStatus,
)


def enum_type(enum_cls, name):
    return SAEnum(enum_cls, name=name, values_callable=lambda x: [e.value for e in x])

class PurchaseRequest(TimestampMixin, Base):
    __tablename__ = "purchase_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    request_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    image_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    image_unique_id: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The core state machine column
    status: Mapped[RequestStatus] = mapped_column(enum_type(RequestStatus, "request_status"), default=RequestStatus.PENDING_ADMIN_APPROVAL)
    accepted_seller_count: Mapped[int] = mapped_column(Integer, default=0)
    # Admin audit fields
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None] = mapped_column(Text)
    settlement_reason: Mapped[str | None] = mapped_column(Text)
    settlement_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    settlement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("seller_offers.id"),
        unique=True,
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchase_requests_quantity_positive"),
        CheckConstraint("accepted_seller_count <= 3", name="ck_purchase_requests_accepted_seller_count_max"),
    )

class RequestSeller(TimestampMixin, Base):
    # Tracks which sellers have been notified for a specific request
    __tablename__ = "request_sellers"
    __table_args__ = (UniqueConstraint("request_id", "seller_id", name="uq_request_seller"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_requests.id"), nullable=False, index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[RequestSellerStatus] = mapped_column(enum_type(RequestSellerStatus, "request_seller_status"), default=RequestSellerStatus.NOTIFIED)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class SellerOffer(TimestampMixin, Base):
    __tablename__ = "seller_offers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_requests.id"), nullable=False, index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    request_seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("request_sellers.id"), unique=True, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="ETB")
    status: Mapped[OfferStatus] = mapped_column(enum_type(OfferStatus, "offer_status"), default=OfferStatus.ACTIVE)
    __table_args__ = (
        UniqueConstraint("request_id", "seller_id", name="uq_seller_offers_request_seller"),
        CheckConstraint("price > 0", name="ck_seller_offers_price_positive"),
        Index(
            "ux_seller_offers_one_selected_per_request",
            "request_id",
            unique=True,
            postgresql_where=text("status = 'SELECTED'"),
        ),
    )


class SupportTicket(TimestampMixin, Base):
    __tablename__ = "support_tickets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(enum_type(TicketStatus, "ticket_status"), default=TicketStatus.OPEN)
    solution: Mapped[str | None] = mapped_column(Text)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_response: Mapped[str | None] = mapped_column(Text)