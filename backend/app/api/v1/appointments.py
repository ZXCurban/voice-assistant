"""Patient/assistant-facing appointment lifecycle endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.models.appointment import Appointment
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentPatientRef,
    AppointmentReschedule,
)
from app.schemas.slot import SlotDoctorRef, SlotRoomRef, SlotSpecialtyRef
from app.services import appointments as appointments_service
from app.services.common import ensure_aware_utc

router = APIRouter(prefix="/api/v1", tags=["appointments"])


def to_out(appointment: Appointment) -> AppointmentOut:
    """Build voice-ready output with nested human context (no lazy loads:
    services return appointments with relations eagerly loaded)."""
    doctor = appointment.doctor
    return AppointmentOut(
        id=appointment.id,
        clinic_id=appointment.clinic_id,
        doctor_id=appointment.doctor_id,
        patient_id=appointment.patient_id,
        room_id=appointment.room_id,
        starts_at=ensure_aware_utc(appointment.starts_at),
        ends_at=ensure_aware_utc(appointment.ends_at),
        status=appointment.status,
        reason=appointment.reason,
        created_at=ensure_aware_utc(appointment.created_at),
        updated_at=ensure_aware_utc(appointment.updated_at),
        doctor=SlotDoctorRef(id=doctor.id, full_name=doctor.full_name),
        specialty=SlotSpecialtyRef(id=doctor.specialty.id, name=doctor.specialty.name),
        patient=AppointmentPatientRef(
            id=appointment.patient.id, full_name=appointment.patient.full_name
        ),
        room=(
            SlotRoomRef(
                id=appointment.room.id,
                code=appointment.room.code,
                label=appointment.room.label,
            )
            if appointment.room is not None
            else None
        ),
    )


@router.post(
    "/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Book an appointment",
    description=(
        "Book a patient into an exact available slot. "
        "Copy starts_at verbatim from GET /slots — arbitrary times are "
        "rejected with 409. ends_at is computed server-side from the "
        "doctor's slot grid and returned in the response."
    ),
)
async def book_appointment(session: SessionDep, data: AppointmentCreate) -> AppointmentOut:
    return to_out(await appointments_service.book_appointment(session, data))


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
    return to_out(await appointments_service.get_appointment(session, clinic_id, appointment_id))


@router.get(
    "/appointments",
    response_model=list[AppointmentOut],
    summary="List appointments",
    description=(
        "Requires clinic_id plus at least one scope filter (patient_id "
        "and/or doctor_id) so one call can never dump another tenant's book."
    ),
)
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
    return [to_out(e) for e in entities]


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentOut,
    summary="Move a booking to a new time",
    description=(
        "Creates a replacement booking at new_starts_at (must be a free "
        "slot from GET /slots) and cancels the old one atomically. "
        "Only booked appointments can be rescheduled."
    ),
)
async def reschedule_appointment(
    session: SessionDep,
    appointment_id: int,
    data: AppointmentReschedule,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    return to_out(
        await appointments_service.reschedule_appointment(
            session, clinic_id, appointment_id, data.new_starts_at
        )
    )


@router.post(
    "/appointments/{appointment_id}/cancel",
    response_model=AppointmentOut,
    summary="Cancel a booking",
    description="Booked → cancelled. Frees the slot for re-booking. Terminal states are immutable.",
)
async def cancel_appointment(
    session: SessionDep,
    appointment_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> AppointmentOut:
    return to_out(await appointments_service.cancel_appointment(session, clinic_id, appointment_id))


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
    return to_out(
        await appointments_service.complete_appointment(session, clinic_id, appointment_id)
    )
