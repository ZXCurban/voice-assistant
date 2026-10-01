"""Specialty / department / room management use-cases."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.department import Department
from app.models.room import Room
from app.models.specialty import Specialty
from app.repositories import catalog as catalog_repo
from app.schemas.catalog import (
    DepartmentCreate,
    DepartmentUpdate,
    RoomCreate,
    RoomUpdate,
    SpecialtyCreate,
    SpecialtyUpdate,
)
from app.services.common import require_clinic
from app.services.common import save_or_conflict as _save

# --- Specialties ---


async def create_specialty(session: AsyncSession, data: SpecialtyCreate) -> Specialty:
    await require_clinic(session, data.clinic_id)
    entity = Specialty(
        clinic_id=data.clinic_id,
        name=data.name.strip().lower(),
        description=data.description,
        active=True,
    )
    session.add(entity)
    await _save(session, "specialty already exists in this clinic")
    await session.refresh(entity)
    return entity


async def get_specialty(session: AsyncSession, clinic_id: int, specialty_id: int) -> Specialty:
    entity = await catalog_repo.get_specialty(session, clinic_id, specialty_id)
    if entity is None:
        raise NotFoundError("specialty not found")
    return entity


async def update_specialty(
    session: AsyncSession, clinic_id: int, specialty_id: int, data: SpecialtyUpdate
) -> Specialty:
    entity = await get_specialty(session, clinic_id, specialty_id)
    if data.name is not None:
        entity.name = data.name.strip().lower()
    if data.description is not None:
        entity.description = data.description
    if data.active is not None:
        entity.active = data.active
    await _save(session, "specialty already exists in this clinic")
    await session.refresh(entity)
    return entity


async def list_specialties(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[Specialty]:
    await require_clinic(session, clinic_id)
    return await catalog_repo.list_specialties(session, clinic_id, active_only=active_only)


# --- Departments ---


async def create_department(session: AsyncSession, data: DepartmentCreate) -> Department:
    await require_clinic(session, data.clinic_id)
    entity = Department(
        clinic_id=data.clinic_id,
        name=data.name.strip(),
        description=data.description,
        floor=data.floor,
        active=True,
    )
    session.add(entity)
    await _save(session, "department already exists in this clinic")
    await session.refresh(entity)
    return entity


async def get_department(session: AsyncSession, clinic_id: int, department_id: int) -> Department:
    entity = await catalog_repo.get_department(session, clinic_id, department_id)
    if entity is None:
        raise NotFoundError("department not found")
    return entity


async def update_department(
    session: AsyncSession, clinic_id: int, department_id: int, data: DepartmentUpdate
) -> Department:
    entity = await get_department(session, clinic_id, department_id)
    if data.name is not None:
        entity.name = data.name.strip()
    if data.description is not None:
        entity.description = data.description
    if data.floor is not None:
        entity.floor = data.floor
    if data.active is not None:
        entity.active = data.active
    await _save(session, "department already exists in this clinic")
    await session.refresh(entity)
    return entity


async def list_departments(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[Department]:
    await require_clinic(session, clinic_id)
    return await catalog_repo.list_departments(session, clinic_id, active_only=active_only)


# --- Rooms ---


async def create_room(session: AsyncSession, data: RoomCreate) -> Room:
    await require_clinic(session, data.clinic_id)
    if data.department_id is not None:
        await get_department(session, data.clinic_id, data.department_id)
    entity = Room(
        clinic_id=data.clinic_id,
        department_id=data.department_id,
        code=data.code.strip(),
        label=data.label,
        floor=data.floor,
        active=True,
    )
    session.add(entity)
    await _save(session, "room code already exists in this clinic")
    await session.refresh(entity)
    return entity


async def get_room(session: AsyncSession, clinic_id: int, room_id: int) -> Room:
    entity = await catalog_repo.get_room(session, clinic_id, room_id)
    if entity is None:
        raise NotFoundError("room not found")
    return entity


async def update_room(
    session: AsyncSession, clinic_id: int, room_id: int, data: RoomUpdate
) -> Room:
    entity = await get_room(session, clinic_id, room_id)
    if data.department_id is not None:
        await get_department(session, clinic_id, data.department_id)
        entity.department_id = data.department_id
    if data.code is not None:
        entity.code = data.code.strip()
    if data.label is not None:
        entity.label = data.label
    if data.floor is not None:
        entity.floor = data.floor
    if data.active is not None:
        entity.active = data.active
    await _save(session, "room code already exists in this clinic")
    await session.refresh(entity)
    return entity


async def list_rooms(
    session: AsyncSession,
    clinic_id: int,
    *,
    department_id: int | None = None,
    active_only: bool = True,
) -> list[Room]:
    await require_clinic(session, clinic_id)
    if department_id is not None:
        await get_department(session, clinic_id, department_id)
    return await catalog_repo.list_rooms(
        session, clinic_id, department_id=department_id, active_only=active_only
    )
