"""Doctor + doctor-schedule management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.doctor import DoctorCreate, DoctorOut, DoctorUpdate
from app.schemas.schedule import (
    DoctorScheduleCreate,
    DoctorScheduleOut,
    DoctorScheduleUpdate,
)
from app.services import doctors as doctors_service
from app.services import schedules as schedules_service

router = APIRouter(prefix="/api/v1/management", tags=["management-doctors"])


@router.post(
    "/doctors",
    response_model=DoctorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a doctor",
)
async def create_doctor(session: SessionDep, data: DoctorCreate) -> DoctorOut:
    return DoctorOut.model_validate(await doctors_service.create_doctor(session, data))


@router.get("/doctors/{doctor_id}", response_model=DoctorOut, summary="Get a doctor")
async def get_doctor(
    session: SessionDep,
    doctor_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DoctorOut:
    return DoctorOut.model_validate(await doctors_service.get_doctor(session, clinic_id, doctor_id))


@router.patch("/doctors/{doctor_id}", response_model=DoctorOut, summary="Update a doctor")
async def update_doctor(
    session: SessionDep,
    doctor_id: int,
    data: DoctorUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DoctorOut:
    return DoctorOut.model_validate(
        await doctors_service.update_doctor(session, clinic_id, doctor_id, data)
    )


@router.post(
    "/doctors/{doctor_id}/schedules",
    response_model=DoctorScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a doctor working interval",
)
async def create_doctor_schedule(
    session: SessionDep,
    doctor_id: int,
    data: DoctorScheduleCreate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DoctorScheduleOut:
    return DoctorScheduleOut.model_validate(
        await schedules_service.create_doctor_schedule(session, clinic_id, doctor_id, data)
    )


@router.get(
    "/doctors/{doctor_id}/schedules",
    response_model=list[DoctorScheduleOut],
    summary="List doctor working hours",
)
async def list_doctor_schedules(
    session: SessionDep,
    doctor_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
    active_only: Annotated[bool, Query()] = True,
) -> list[DoctorScheduleOut]:
    entities = await schedules_service.list_doctor_schedules(
        session, clinic_id, doctor_id, active_only=active_only
    )
    return [DoctorScheduleOut.model_validate(e) for e in entities]


@router.patch(
    "/doctor-schedules/{schedule_id}",
    response_model=DoctorScheduleOut,
    summary="Update a doctor working interval",
)
async def update_doctor_schedule(
    session: SessionDep,
    schedule_id: int,
    data: DoctorScheduleUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DoctorScheduleOut:
    return DoctorScheduleOut.model_validate(
        await schedules_service.update_doctor_schedule(session, clinic_id, schedule_id, data)
    )


@router.delete(
    "/doctor-schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a doctor working interval",
)
async def delete_doctor_schedule(
    session: SessionDep,
    schedule_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> None:
    await schedules_service.delete_doctor_schedule(session, clinic_id, schedule_id)
