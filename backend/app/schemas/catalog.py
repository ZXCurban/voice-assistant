"""Specialty / department / room request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SpecialtyCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=5000)


class SpecialtyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    active: bool | None = None


class SpecialtyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    name: str
    description: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class DepartmentCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    floor: int | None = Field(default=None, ge=-5, le=200)


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    floor: int | None = Field(default=None, ge=-5, le=200)
    active: bool | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    name: str
    description: str | None
    floor: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


class RoomCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    department_id: int | None = Field(default=None, gt=0)
    code: str = Field(min_length=1, max_length=30)
    label: str | None = Field(default=None, max_length=200)
    floor: int | None = Field(default=None, ge=-5, le=200)


class RoomUpdate(BaseModel):
    department_id: int | None = Field(default=None, gt=0)
    code: str | None = Field(default=None, min_length=1, max_length=30)
    label: str | None = Field(default=None, max_length=200)
    floor: int | None = Field(default=None, ge=-5, le=200)
    active: bool | None = None


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    department_id: int | None
    code: str
    label: str | None
    floor: int | None
    active: bool
    created_at: datetime
    updated_at: datetime
