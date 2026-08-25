# app/db/base.py
# LAYER: Infrastructure / Database
# PURPOSE: Defines the SQLAlchemy Base class and reusable column mixins.
# WHY HERE: Separates ORM configuration from the actual model definitions.

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # The foundational class for all SQLAlchemy ORM models
    metadata = MetaData()

class TimestampMixin:
    # MIXIN PATTERN: Automatically adds created_at and updated_at columns to any model that inherits this.
    # WHY: Prevents code duplication. Every table needs audit timestamps.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())