"""Declarative base and shared column helpers for ORM models."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class all ORM models must inherit from."""


def created_at_col() -> Mapped[datetime]:
    """Creation timestamp (UTC, set by the database)."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_col() -> Mapped[datetime]:
    """Update timestamp (UTC, set by the database on write)."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
