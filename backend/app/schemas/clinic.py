"""Clinic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClinicCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    timezone: str = Field(min_length=1, max_length=100)


class ClinicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None


class ClinicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    address: str | None
    phone: str | None
    timezone: str
    active: bool
    created_at: datetime
    updated_at: datetime
