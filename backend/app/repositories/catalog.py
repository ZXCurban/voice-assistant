"""Specialty / department / room persistence (always clinic-scoped)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.room import Room
from app.models.specialty import Specialty


async def get_specialty(
    session: AsyncSession, clinic_id: int, specialty_id: int
) -> Specialty | None:
    stmt = select(Specialty).where(Specialty.id == specialty_id, Specialty.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_specialties(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[Specialty]:
    stmt = (
        select(Specialty)
        .where(Specialty.clinic_id == clinic_id)
        .order_by(Specialty.name, Specialty.id)
    )
    if active_only:
        stmt = stmt.where(Specialty.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_department(
    session: AsyncSession, clinic_id: int, department_id: int
) -> Department | None:
    stmt = select(Department).where(
        Department.id == department_id, Department.clinic_id == clinic_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_departments(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[Department]:
    stmt = (
        select(Department)
        .where(Department.clinic_id == clinic_id)
        .order_by(Department.name, Department.id)
    )
    if active_only:
        stmt = stmt.where(Department.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_room(session: AsyncSession, clinic_id: int, room_id: int) -> Room | None:
    stmt = select(Room).where(Room.id == room_id, Room.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_rooms(
    session: AsyncSession,
    clinic_id: int,
    *,
    department_id: int | None = None,
    active_only: bool = True,
) -> list[Room]:
    stmt = select(Room).where(Room.clinic_id == clinic_id).order_by(Room.code, Room.id)
    if department_id is not None:
        stmt = stmt.where(Room.department_id == department_id)
    if active_only:
        stmt = stmt.where(Room.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def add(session: AsyncSession, entity: Specialty | Department | Room) -> None:
    session.add(entity)
    await session.flush()
