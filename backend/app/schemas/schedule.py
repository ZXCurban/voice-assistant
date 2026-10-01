"""Weekly schedule + exception request/response schemas."""

from datetime import date as date_type
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClinicScheduleCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_local: time
    end_local: time

    @model_validator(mode="after")
    def _check_interval(self) -> "ClinicScheduleCreate":
        if self.start_local >= self.end_local:
            raise ValueError("start_local must be before end_local")
        return self


class ClinicScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    weekday: int
    start_local: time
    end_local: time
    active: bool
    created_at: datetime
    updated_at: datetime


class ClinicScheduleUpdate(BaseModel):
    start_local: time | None = None
    end_local: time | None = None
    active: bool | None = None


class DoctorScheduleCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_local: time
    end_local: time
    slot_minutes: int = Field(default=20)
    room_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_interval(self) -> "DoctorScheduleCreate":
        if self.start_local >= self.end_local:
            raise ValueError("start_local must be before end_local")
        if self.slot_minutes not in (5, 10, 15, 20, 30, 45, 60):
            raise ValueError("slot_minutes must be one of 5,10,15,20,30,45,60")
        return self


class DoctorScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    doctor_id: int
    weekday: int
    start_local: time
    end_local: time
    slot_minutes: int
    room_id: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


class DoctorScheduleUpdate(BaseModel):
    """Partial update. room_id=None means unchanged (no detach in MVP)."""

    start_local: time | None = None
    end_local: time | None = None
    slot_minutes: int | None = Field(default=None)
    room_id: int | None = Field(default=None, gt=0)
    active: bool | None = None

    @model_validator(mode="after")
    def _check_slot(self) -> "DoctorScheduleUpdate":
        if self.slot_minutes is not None and self.slot_minutes not in (
            5,
            10,
            15,
            20,
            30,
            45,
            60,
        ):
            raise ValueError("slot_minutes must be one of 5,10,15,20,30,45,60")
        return self


class ScheduleExceptionCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    doctor_id: int | None = Field(default=None, gt=0)
    date: date_type
    kind: str = Field(pattern="^(day_off|custom_hours)$")
    start_local: time | None = None
    end_local: time | None = None
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _check_times(self) -> "ScheduleExceptionCreate":
        if self.kind == "day_off" and (self.start_local is not None or self.end_local is not None):
            raise ValueError("day_off must not carry start/end times")
        if self.kind == "custom_hours":
            if self.start_local is None or self.end_local is None:
                raise ValueError("custom_hours requires start_local and end_local")
            if self.start_local >= self.end_local:
                raise ValueError("start_local must be before end_local")
        return self


class ScheduleExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    doctor_id: int | None
    date: date_type
    kind: str
    start_local: time | None
    end_local: time | None
    reason: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleExceptionUpdate(BaseModel):
    """Partial update. Switching kind to day_off clears times; date/doctor
    are immutable (they form the uniqueness scope)."""

    kind: str | None = Field(default=None, pattern="^(day_off|custom_hours)$")
    start_local: time | None = None
    end_local: time | None = None
    reason: str | None = Field(default=None, max_length=300)
