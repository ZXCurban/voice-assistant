"""Schedule-exception persistence (always clinic-scoped)."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule_exception import ScheduleException


async def get_exception(
    session: AsyncSession, clinic_id: int, exception_id: int
) -> ScheduleException | None:
    stmt = select(ScheduleException).where(
        ScheduleException.id == exception_id,
        ScheduleException.clinic_id == clinic_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_exceptions(
    session: AsyncSession,
    clinic_id: int,
    *,
    doctor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ScheduleException]:
    stmt = (
        select(ScheduleException)
        .where(ScheduleException.clinic_id == clinic_id)
        .order_by(ScheduleException.date, ScheduleException.id)
    )
    if doctor_id is not None:
        stmt = stmt.where(ScheduleException.doctor_id == doctor_id)
    if date_from is not None:
        stmt = stmt.where(ScheduleException.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(ScheduleException.date <= date_to)
    return list((await session.execute(stmt)).scalars().all())


async def get_exceptions_for_date(
    session: AsyncSession, clinic_id: int, target: date
) -> list[ScheduleException]:
    """All overrides affecting a clinic date (clinic-wide + every doctor)."""
    stmt = select(ScheduleException).where(
        ScheduleException.clinic_id == clinic_id, ScheduleException.date == target
    )
    return list((await session.execute(stmt)).scalars().all())
