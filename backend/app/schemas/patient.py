"""Patient request/response schemas (profile only, no medical data)."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    clinic_id: int = Field(gt=0)
    full_name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=5, max_length=50)
    birth_date: date | None = None


class PatientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, min_length=5, max_length=50)
    birth_date: date | None = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    full_name: str
    phone: str
    birth_date: date | None
    created_at: datetime
    updated_at: datetime
