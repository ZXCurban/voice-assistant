"""Clinic + clinic-schedule management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.clinic import ClinicCreate, ClinicOut, ClinicUpdate
from app.schemas.schedule import ClinicScheduleCreate, ClinicScheduleOut
from app.services import clinics as clinics_service
from app.services import schedules as schedules_service

router = APIRouter(prefix="/api/v1/management", tags=["management-clinics"])


@router.post(
    "/clinics",
    response_model=ClinicOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a clinic",
)
async def create_clinic(session: SessionDep, data: ClinicCreate) -> ClinicOut:
    return ClinicOut.model_validate(await clinics_service.create_clinic(session, data))


@router.get("/clinics", response_model=list[ClinicOut], summary="List all clinics")
async def list_clinics(
    session: SessionDep,
    active_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ClinicOut]:
    entities = await clinics_service.list_clinics(
        session, active_only=active_only, limit=limit, offset=offset
    )
    return [ClinicOut.model_validate(e) for e in entities]


@router.get("/clinics/{clinic_id}", response_model=ClinicOut, summary="Get clinic for editing")
async def get_clinic(session: SessionDep, clinic_id: int) -> ClinicOut:
    return ClinicOut.model_validate(await clinics_service.get_clinic(session, clinic_id))


@router.patch("/clinics/{clinic_id}", response_model=ClinicOut, summary="Update a clinic")
async def update_clinic(session: SessionDep, clinic_id: int, data: ClinicUpdate) -> ClinicOut:
    return ClinicOut.model_validate(await clinics_service.update_clinic(session, clinic_id, data))


@router.post(
    "/clinics/{clinic_id}/schedule",
    response_model=ClinicScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a clinic opening interval",
)
async def create_clinic_schedule(
    session: SessionDep, clinic_id: int, data: ClinicScheduleCreate
) -> ClinicScheduleOut:
    return ClinicScheduleOut.model_validate(
        await schedules_service.create_clinic_schedule(session, clinic_id, data)
    )


@router.get(
    "/clinics/{clinic_id}/schedule",
    response_model=list[ClinicScheduleOut],
    summary="List clinic opening hours",
)
async def list_clinic_schedules(session: SessionDep, clinic_id: int) -> list[ClinicScheduleOut]:
    entities = await schedules_service.list_clinic_schedules(session, clinic_id)
    return [ClinicScheduleOut.model_validate(e) for e in entities]


@router.delete(
    "/clinic-schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a clinic opening interval",
)
async def delete_clinic_schedule(
    session: SessionDep,
    schedule_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> None:
    await schedules_service.delete_clinic_schedule(session, clinic_id, schedule_id)
