"""Schedule-exception (day off / custom hours) management use-cases."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.schedule_exception import ScheduleException
from app.repositories import exceptions as exceptions_repo
from app.schemas.schedule import ScheduleExceptionCreate
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
