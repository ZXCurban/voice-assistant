"""Appointment request/response schemas."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.slot import SlotDoctorRef, SlotRoomRef, SlotSpecialtyRef


class AppointmentCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    doctor_id: int = Field(gt=0)
    patient_id: int = Field(gt=0)
    # Must be timezone-aware; ends_at is computed server-side from the
    # doctor's slot grid.
    starts_at: AwareDatetime
    room_id: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=300)


class AppointmentReschedule(BaseModel):
    new_starts_at: AwareDatetime


class AppointmentPatientRef(BaseModel):
    id: int
    full_name: str


class AppointmentOut(BaseModel):
    """Booking with human-readable nested context for voice presentation.

    Nested blocks are additive context (ids stay top-level). Built
    manually in the router (not model_validate) because specialty
    resolves through doctor.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    doctor_id: int
    patient_id: int
    room_id: int | None
    starts_at: datetime
    ends_at: datetime
    status: str
    reason: str | None
    created_at: datetime
    updated_at: datetime
    doctor: SlotDoctorRef
    specialty: SlotSpecialtyRef
    patient: AppointmentPatientRef
    room: SlotRoomRef | None = None
