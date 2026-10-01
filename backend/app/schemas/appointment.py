"""Appointment request/response schemas."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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


class AppointmentOut(BaseModel):
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
