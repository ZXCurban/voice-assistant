"""Pydantic contracts for the multi-clinic platform API."""

from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentReschedule,
)
from app.schemas.catalog import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    SpecialtyCreate,
    SpecialtyOut,
    SpecialtyUpdate,
)
from app.schemas.clinic import ClinicCreate, ClinicOut, ClinicUpdate
from app.schemas.doctor import DoctorCreate, DoctorDetailOut, DoctorOut, DoctorUpdate
from app.schemas.health import HealthResponse
from app.schemas.patient import PatientCreate, PatientOut, PatientUpdate
from app.schemas.schedule import (
    ClinicScheduleCreate,
    ClinicScheduleOut,
    DoctorScheduleCreate,
    DoctorScheduleOut,
    ScheduleExceptionCreate,
    ScheduleExceptionOut,
)
from app.schemas.slot import SlotOut

__all__ = [
    "AppointmentCreate",
    "AppointmentOut",
    "AppointmentReschedule",
    "ClinicCreate",
    "ClinicOut",
    "ClinicUpdate",
    "ClinicScheduleCreate",
    "ClinicScheduleOut",
    "DepartmentCreate",
    "DepartmentOut",
    "DepartmentUpdate",
    "DoctorCreate",
    "DoctorDetailOut",
    "DoctorOut",
    "DoctorScheduleCreate",
    "DoctorScheduleOut",
    "DoctorUpdate",
    "HealthResponse",
    "PatientCreate",
    "PatientOut",
    "PatientUpdate",
    "RoomCreate",
    "RoomOut",
    "RoomUpdate",
    "ScheduleExceptionCreate",
    "ScheduleExceptionOut",
    "SlotOut",
    "SpecialtyCreate",
    "SpecialtyOut",
    "SpecialtyUpdate",
]
