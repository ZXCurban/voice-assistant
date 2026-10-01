"""Shared service helpers: scope enforcement, time handling, DB errors."""

from datetime import UTC, datetime
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.clinic import Clinic
from app.repositories import clinics as clinics_repo

T = TypeVar("T")


def ensure_aware_utc(value: datetime) -> datetime:
    """Normalize DB datetimes to aware UTC (SQLite returns naive)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_timezone(name: str) -> str:
    """Return stripped IANA name or raise ValueError (→ 422)."""
    cleaned = name.strip()
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid timezone: {name!r}") from exc
    return cleaned


async def require_clinic(session: AsyncSession, clinic_id: int) -> Clinic:
    """Load clinic or raise NotFoundError (→ 404, incl. cross-tenant)."""
    clinic = await clinics_repo.get_clinic(session, clinic_id)
    if clinic is None:
        raise NotFoundError("clinic not found")
    return clinic


async def flush_or_conflict(session: AsyncSession, message: str) -> None:
    """Flush pending writes, mapping unique violations to ConflictError."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(message) from exc


async def save_or_conflict(session: AsyncSession, message: str) -> None:
    """Flush + commit, mapping unique violations to ConflictError."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(message) from exc
    await session.commit()
