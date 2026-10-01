"""Pydantic contracts for the multi-clinic platform API."""

from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentPatientRef,
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
    ClinicScheduleUpdate,
    DoctorScheduleCreate,
    DoctorScheduleOut,
    DoctorScheduleUpdate,
    ScheduleExceptionCreate,
    ScheduleExceptionOut,
    ScheduleExceptionUpdate,
)
from app.schemas.slot import SlotOut

__all__ = [
    "AppointmentCreate",
    "AppointmentOut",
    "AppointmentPatientRef",
    "AppointmentReschedule",
    "ClinicCreate",
    "ClinicOut",
    "ClinicUpdate",
    "ClinicScheduleCreate",
    "ClinicScheduleOut",
    "ClinicScheduleUpdate",
    "DepartmentCreate",
    "DepartmentOut",
    "DepartmentUpdate",
    "DoctorCreate",
    "DoctorDetailOut",
    "DoctorOut",
    "DoctorScheduleCreate",
    "DoctorScheduleOut",
    "DoctorScheduleUpdate",
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
    "ScheduleExceptionUpdate",
    "SlotOut",
    "SpecialtyCreate",
    "SpecialtyOut",
    "SpecialtyUpdate",
]
