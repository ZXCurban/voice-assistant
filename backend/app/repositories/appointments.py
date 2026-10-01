"""Appointment persistence (always clinic-scoped)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import STATUS_BOOKED, Appointment


async def get_appointment(
    session: AsyncSession, clinic_id: int, appointment_id: int
) -> Appointment | None:
    stmt = select(Appointment).where(
        Appointment.id == appointment_id, Appointment.clinic_id == clinic_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_appointments(
    session: AsyncSession,
    clinic_id: int,
    *,
    patient_id: int | None = None,
    doctor_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Appointment]:
    stmt = (
        select(Appointment)
        .where(Appointment.clinic_id == clinic_id)
        .order_by(Appointment.starts_at, Appointment.id)
        .offset(offset)
        .limit(limit)
    )
    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if doctor_id is not None:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if date_from is not None:
        stmt = stmt.where(Appointment.starts_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Appointment.starts_at <= date_to)
    if status is not None:
        stmt = stmt.where(Appointment.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def list_booked_starts(
    session: AsyncSession, doctor_id: int, day_start: datetime, day_end: datetime
) -> list[datetime]:
    """Booked appointment instants for a doctor inside [day_start, day_end)."""
    stmt = select(Appointment.starts_at).where(
        Appointment.doctor_id == doctor_id,
        Appointment.status == STATUS_BOOKED,
        Appointment.starts_at >= day_start,
        Appointment.starts_at < day_end,
    )
    return list((await session.execute(stmt)).scalars().all())
