"""Patient/assistant-facing appointment lifecycle endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentReschedule,
)
from app.services import appointments as appointments_service

router = APIRouter(prefix="/api/v1", tags=["appointments"])


@router.post(
    "/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Book an appointment",
)
async def book_appointment(session: SessionDep, data: AppointmentCreate) -> AppointmentOut:
    appointment = await appointments_service.book_appointment(session, data)
    return AppointmentOut.model_validate(appointment)


@router.get(
    "/appointments/{appointment_id}",
    response_model=AppointmentOut,
    summary="View one appointment",
)
async def get_appointment(
    session: SessionDep,
    appointment_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    appointment = await appointments_service.get_appointment(session, clinic_id, appointment_id)
    return AppointmentOut.model_validate(appointment)


@router.get("/appointments", response_model=list[AppointmentOut], summary="List appointments")
async def list_appointments(
    session: SessionDep,
    clinic_id: Annotated[int, Query(gt=0)],
    patient_id: Annotated[int | None, Query(gt=0)] = None,
    doctor_id: Annotated[int | None, Query(gt=0)] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    appointment_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AppointmentOut]:
    entities = await appointments_service.list_appointments(
        session,
        clinic_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        date_from=date_from,
        date_to=date_to,
        status=appointment_status,
        limit=limit,
        offset=offset,
    )
    return [AppointmentOut.model_validate(e) for e in entities]


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentOut,
    summary="Move a booking to a new time",
)
async def reschedule_appointment(
    session: SessionDep,
    appointment_id: int,
    data: AppointmentReschedule,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    appointment = await appointments_service.reschedule_appointment(
        session, clinic_id, appointment_id, data.new_starts_at
    )
    return AppointmentOut.model_validate(appointment)


@router.post(
    "/appointments/{appointment_id}/cancel",
    response_model=AppointmentOut,
    summary="Cancel a booking",
)
async def cancel_appointment(
    session: SessionDep,
    appointment_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    appointment = await appointments_service.cancel_appointment(session, clinic_id, appointment_id)
    return AppointmentOut.model_validate(appointment)


@router.post(
    "/appointments/{appointment_id}/complete",
    response_model=AppointmentOut,
    summary="Mark a booking completed",
)
async def complete_appointment(
    session: SessionDep,
    appointment_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    appointment = await appointments_service.complete_appointment(
        session, clinic_id, appointment_id
    )
    return AppointmentOut.model_validate(appointment)
