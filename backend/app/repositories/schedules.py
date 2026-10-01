"""Clinic / doctor weekly-schedule persistence (always clinic-scoped)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinic_schedule import ClinicSchedule
from app.models.doctor_schedule import DoctorSchedule


async def list_clinic_schedules(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[ClinicSchedule]:
    stmt = (
        select(ClinicSchedule)
        .where(ClinicSchedule.clinic_id == clinic_id)
        .order_by(ClinicSchedule.weekday, ClinicSchedule.start_local)
    )
    if active_only:
        stmt = stmt.where(ClinicSchedule.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_clinic_schedule(
    session: AsyncSession, clinic_id: int, schedule_id: int
) -> ClinicSchedule | None:
    stmt = select(ClinicSchedule).where(
        ClinicSchedule.id == schedule_id, ClinicSchedule.clinic_id == clinic_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_doctor_schedules(
    session: AsyncSession,
    clinic_id: int,
    doctor_id: int,
    *,
    active_only: bool = True,
) -> list[DoctorSchedule]:
    stmt = (
        select(DoctorSchedule)
        .where(
            DoctorSchedule.clinic_id == clinic_id,
            DoctorSchedule.doctor_id == doctor_id,
        )
        .order_by(DoctorSchedule.weekday, DoctorSchedule.start_local)
    )
    if active_only:
        stmt = stmt.where(DoctorSchedule.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_doctor_schedule(
    session: AsyncSession, clinic_id: int, schedule_id: int
) -> DoctorSchedule | None:
    stmt = select(DoctorSchedule).where(
        DoctorSchedule.id == schedule_id, DoctorSchedule.clinic_id == clinic_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
