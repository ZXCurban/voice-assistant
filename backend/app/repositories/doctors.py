"""Doctor persistence (always clinic-scoped)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor import Doctor


async def get_doctor(session: AsyncSession, clinic_id: int, doctor_id: int) -> Doctor | None:
    stmt = select(Doctor).where(Doctor.id == doctor_id, Doctor.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_doctor_detail(session: AsyncSession, clinic_id: int, doctor_id: int) -> Doctor | None:
    stmt = (
        select(Doctor)
        .where(Doctor.id == doctor_id, Doctor.clinic_id == clinic_id)
        .options(
            selectinload(Doctor.specialty),
            selectinload(Doctor.department),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_doctors(
    session: AsyncSession,
    clinic_id: int,
    *,
    specialty_id: int | None = None,
    department_id: int | None = None,
    active_only: bool = True,
) -> list[Doctor]:
    stmt = (
        select(Doctor)
        .where(Doctor.clinic_id == clinic_id)
        .order_by(Doctor.full_name, Doctor.id)
        .options(selectinload(Doctor.specialty), selectinload(Doctor.department))
    )
    if specialty_id is not None:
        stmt = stmt.where(Doctor.specialty_id == specialty_id)
    if department_id is not None:
        stmt = stmt.where(Doctor.department_id == department_id)
    if active_only:
        stmt = stmt.where(Doctor.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def add_doctor(session: AsyncSession, doctor: Doctor) -> None:
    session.add(doctor)
    await session.flush()
