"""ORM models registry. Import all entities here so Alembic sees them."""

from app.models.appointment import Appointment
from app.models.base import Base
from app.models.clinic import Clinic
from app.models.clinic_schedule import ClinicSchedule
from app.models.department import Department
from app.models.doctor import Doctor
from app.models.doctor_schedule import DoctorSchedule
from app.models.patient import Patient
from app.models.room import Room
from app.models.schedule_exception import ScheduleException
from app.models.specialty import Specialty

__all__ = [
    "Appointment",
    "Base",
    "Clinic",
    "ClinicSchedule",
    "Department",
    "Doctor",
    "DoctorSchedule",
    "Patient",
    "Room",
    "ScheduleException",
    "Specialty",
]
