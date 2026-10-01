"""Schedule-exception (holidays / days off) management endpoints."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.schedule import (
    ScheduleExceptionCreate,
    ScheduleExceptionOut,
    ScheduleExceptionUpdate,
)
from app.services import exceptions as exceptions_service

router = APIRouter(prefix="/api/v1/management", tags=["management-exceptions"])


@router.post(
    "/schedule-exceptions",
    response_model=ScheduleExceptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a day-off or custom-hours override",
)
async def create_exception(
    session: SessionDep, data: ScheduleExceptionCreate
) -> ScheduleExceptionOut:
    return ScheduleExceptionOut.model_validate(
        await exceptions_service.create_exception(session, data)
    )


@router.get(
    "/schedule-exceptions",
    response_model=list[ScheduleExceptionOut],
    summary="List schedule exceptions",
)
async def list_exceptions(
    session: SessionDep,
    clinic_id: Annotated[int, Query(gt=0)],
    doctor_id: Annotated[int | None, Query(gt=0)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[ScheduleExceptionOut]:
    entities = await exceptions_service.list_exceptions(
        session, clinic_id, doctor_id=doctor_id, date_from=date_from, date_to=date_to
    )
    return [ScheduleExceptionOut.model_validate(e) for e in entities]


@router.delete(
    "/schedule-exceptions/{exception_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a schedule exception",
)
async def delete_exception(
    session: SessionDep,
    exception_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> None:
    await exceptions_service.delete_exception(session, clinic_id, exception_id)


@router.patch(
    "/schedule-exceptions/{exception_id}",
    response_model=ScheduleExceptionOut,
    summary="Update a schedule exception",
)
async def update_exception(
    session: SessionDep,
    exception_id: int,
    data: ScheduleExceptionUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> ScheduleExceptionOut:
    return ScheduleExceptionOut.model_validate(
        await exceptions_service.update_exception(session, clinic_id, exception_id, data)
    )
