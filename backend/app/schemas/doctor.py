"""Doctor request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.catalog import DepartmentOut, SpecialtyOut


class DoctorCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    full_name: str = Field(min_length=2, max_length=200)
    specialty_id: int = Field(gt=0)
    department_id: int | None = Field(default=None, gt=0)
    phone: str | None = Field(default=None, max_length=50)


class DoctorUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    specialty_id: int | None = Field(default=None, gt=0)
    department_id: int | None = Field(default=None, gt=0)
    phone: str | None = Field(default=None, max_length=50)
    active: bool | None = None


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    full_name: str
    specialty_id: int
    department_id: int | None
    phone: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class DoctorDetailOut(DoctorOut):
    specialty: SpecialtyOut
    department: DepartmentOut | None = None
