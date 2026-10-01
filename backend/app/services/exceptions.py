"""Schedule-exception (day off / custom hours) management use-cases."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.schedule_exception import (
    EXCEPTION_CUSTOM_HOURS,
    EXCEPTION_DAY_OFF,
    ScheduleException,
)
from app.repositories import exceptions as exceptions_repo
from app.schemas.schedule import ScheduleExceptionCreate, ScheduleExceptionUpdate
from app.services import doctors as doctors_service
from app.services.common import require_clinic, save_or_conflict


async def create_exception(
    session: AsyncSession, data: ScheduleExceptionCreate
) -> ScheduleException:
    await require_clinic(session, data.clinic_id)
    if data.doctor_id is not None:
        # Scope check: doctor must belong to the same clinic.
        await doctors_service.get_doctor(session, data.clinic_id, data.doctor_id)
    entity = ScheduleException(
        clinic_id=data.clinic_id,
        doctor_id=data.doctor_id,
        date=data.date,
        kind=data.kind,
        start_local=data.start_local,
        end_local=data.end_local,
        reason=data.reason,
    )
    session.add(entity)
    await save_or_conflict(session, "schedule exception already exists for this date")
    await session.refresh(entity)
    return entity


async def list_exceptions(
    session: AsyncSession,
    clinic_id: int,
    *,
    doctor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ScheduleException]:
    await require_clinic(session, clinic_id)
    if doctor_id is not None:
        await doctors_service.get_doctor(session, clinic_id, doctor_id)
    return await exceptions_repo.list_exceptions(
        session, clinic_id, doctor_id=doctor_id, date_from=date_from, date_to=date_to
    )


async def delete_exception(session: AsyncSession, clinic_id: int, exception_id: int) -> None:
    entity = await exceptions_repo.get_exception(session, clinic_id, exception_id)
    if entity is None:
        raise NotFoundError("schedule exception not found")
    await session.delete(entity)
    await session.commit()


async def update_exception(
    session: AsyncSession, clinic_id: int, exception_id: int, data: ScheduleExceptionUpdate
) -> ScheduleException:
    entity = await exceptions_repo.get_exception(session, clinic_id, exception_id)
    if entity is None:
        raise NotFoundError("schedule exception not found")
    kind = data.kind if data.kind is not None else entity.kind
    if kind == EXCEPTION_DAY_OFF:
        if data.start_local is not None or data.end_local is not None:
            raise ValueError("day_off must not carry start/end times")
        entity.kind = kind
        entity.start_local = None
        entity.end_local = None
    elif kind == EXCEPTION_CUSTOM_HOURS:
        start = data.start_local if data.start_local is not None else entity.start_local
        end = data.end_local if data.end_local is not None else entity.end_local
        if start is None or end is None:
            raise ValueError("custom_hours requires start_local and end_local")
        if start >= end:
            raise ValueError("start_local must be before end_local")
        entity.kind = kind
        entity.start_local = start
        entity.end_local = end
    if data.reason is not None:
        entity.reason = data.reason
    await save_or_conflict(session, "could not update schedule exception")
    await session.refresh(entity)
    return entity
