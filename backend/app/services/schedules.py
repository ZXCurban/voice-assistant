"""Weekly clinic/doctor schedule management use-cases."""

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.clinic_schedule import ClinicSchedule
from app.models.doctor_schedule import DoctorSchedule
from app.repositories import schedules as schedules_repo
from app.schemas.schedule import (
    ClinicScheduleCreate,
    ClinicScheduleUpdate,
    DoctorScheduleCreate,
    DoctorScheduleUpdate,
)
from app.services import doctors as doctors_service
from app.services.catalog import get_room
from app.services.common import require_clinic, save_or_conflict


def _overlaps(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and start_b < end_a


async def create_clinic_schedule(
    session: AsyncSession, clinic_id: int, data: ClinicScheduleCreate
) -> ClinicSchedule:
    await require_clinic(session, clinic_id)
    existing = await schedules_repo.list_clinic_schedules(session, clinic_id, active_only=False)
    for row in existing:
        if not row.active or row.weekday != data.weekday:
            continue
        if _overlaps(data.start_local, data.end_local, row.start_local, row.end_local):
            raise ConflictError("clinic schedule overlaps an existing interval")
    entity = ClinicSchedule(
        clinic_id=clinic_id,
        weekday=data.weekday,
        start_local=data.start_local,
        end_local=data.end_local,
        active=True,
    )
    session.add(entity)
    await save_or_conflict(session, "could not create clinic schedule")
    await session.refresh(entity)
    return entity


async def list_clinic_schedules(
    session: AsyncSession, clinic_id: int, *, active_only: bool = True
) -> list[ClinicSchedule]:
    await require_clinic(session, clinic_id)
    return await schedules_repo.list_clinic_schedules(session, clinic_id, active_only=active_only)


async def update_clinic_schedule(
    session: AsyncSession, clinic_id: int, schedule_id: int, data: ClinicScheduleUpdate
) -> ClinicSchedule:
    entity = await schedules_repo.get_clinic_schedule(session, clinic_id, schedule_id)
    if entity is None:
        raise NotFoundError("clinic schedule not found")
    start = data.start_local if data.start_local is not None else entity.start_local
    end = data.end_local if data.end_local is not None else entity.end_local
    if start >= end:
        raise ValueError("start_local must be before end_local")
    will_be_active = data.active if data.active is not None else entity.active
    if will_be_active:
        existing = await schedules_repo.list_clinic_schedules(session, clinic_id, active_only=False)
        for row in existing:
            if row.id == entity.id or not row.active or row.weekday != entity.weekday:
                continue
            if _overlaps(start, end, row.start_local, row.end_local):
                raise ConflictError("clinic schedule overlaps an existing interval")
    entity.start_local = start
    entity.end_local = end
    entity.active = will_be_active
    await save_or_conflict(session, "could not update clinic schedule")
    await session.refresh(entity)
    return entity


async def delete_clinic_schedule(session: AsyncSession, clinic_id: int, schedule_id: int) -> None:
    entity = await schedules_repo.get_clinic_schedule(session, clinic_id, schedule_id)
    if entity is None:
        raise NotFoundError("clinic schedule not found")
    await session.delete(entity)
    await session.commit()


async def create_doctor_schedule(
    session: AsyncSession, clinic_id: int, doctor_id: int, data: DoctorScheduleCreate
) -> DoctorSchedule:
    await require_clinic(session, clinic_id)
    await doctors_service.get_doctor(session, clinic_id, doctor_id)
    if data.room_id is not None:
        room = await get_room(session, clinic_id, data.room_id)
        if not room.active:
            raise ConflictError("room is inactive")
    existing = await schedules_repo.list_doctor_schedules(
        session, clinic_id, doctor_id, active_only=False
    )
    for row in existing:
        if not row.active or row.weekday != data.weekday:
            continue
        if _overlaps(data.start_local, data.end_local, row.start_local, row.end_local):
            raise ConflictError("doctor schedule overlaps an existing interval")
        if row.slot_minutes != data.slot_minutes:
            raise ConflictError(
                "slot_minutes must be the same for all intervals of a doctor on one weekday"
            )
    entity = DoctorSchedule(
        clinic_id=clinic_id,
        doctor_id=doctor_id,
        weekday=data.weekday,
        start_local=data.start_local,
        end_local=data.end_local,
        slot_minutes=data.slot_minutes,
        room_id=data.room_id,
        active=True,
    )
    session.add(entity)
    await save_or_conflict(session, "could not create doctor schedule")
    await session.refresh(entity)
    return entity


async def list_doctor_schedules(
    session: AsyncSession, clinic_id: int, doctor_id: int, *, active_only: bool = True
) -> list[DoctorSchedule]:
    await require_clinic(session, clinic_id)
    await doctors_service.get_doctor(session, clinic_id, doctor_id)
    return await schedules_repo.list_doctor_schedules(
        session, clinic_id, doctor_id, active_only=active_only
    )


async def update_doctor_schedule(
    session: AsyncSession, clinic_id: int, schedule_id: int, data: DoctorScheduleUpdate
) -> DoctorSchedule:
    entity = await schedules_repo.get_doctor_schedule(session, clinic_id, schedule_id)
    if entity is None:
        raise NotFoundError("doctor schedule not found")
    start = data.start_local if data.start_local is not None else entity.start_local
    end = data.end_local if data.end_local is not None else entity.end_local
    if start >= end:
        raise ValueError("start_local must be before end_local")
    slot = data.slot_minutes if data.slot_minutes is not None else entity.slot_minutes
    will_be_active = data.active if data.active is not None else entity.active
    if data.room_id is not None:
        room = await get_room(session, clinic_id, data.room_id)
        if not room.active:
            raise ConflictError("room is inactive")
        entity.room_id = data.room_id
    if will_be_active:
        existing = await schedules_repo.list_doctor_schedules(
            session, clinic_id, entity.doctor_id, active_only=False
        )
        for row in existing:
            if row.id == entity.id or not row.active or row.weekday != entity.weekday:
                continue
            if _overlaps(start, end, row.start_local, row.end_local):
                raise ConflictError("doctor schedule overlaps an existing interval")
            if row.slot_minutes != slot:
                raise ConflictError(
                    "slot_minutes must be the same for all intervals of a doctor on one weekday"
                )
    entity.start_local = start
    entity.end_local = end
    entity.slot_minutes = slot
    entity.active = will_be_active
    await save_or_conflict(session, "could not update doctor schedule")
    await session.refresh(entity)
    return entity


async def delete_doctor_schedule(session: AsyncSession, clinic_id: int, schedule_id: int) -> None:
    entity = await schedules_repo.get_doctor_schedule(session, clinic_id, schedule_id)
    if entity is None:
        raise NotFoundError("doctor schedule not found")
    await session.delete(entity)
    await session.commit()
