"""Shared builders for integration tests (services-level, SQLite)."""

from datetime import date, time, timedelta
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.catalog import DepartmentCreate, RoomCreate, SpecialtyCreate
from app.schemas.clinic import ClinicCreate
from app.schemas.doctor import DoctorCreate
from app.schemas.patient import PatientCreate
from app.schemas.schedule import ClinicScheduleCreate, DoctorScheduleCreate
from app.services import catalog as catalog_service
from app.services import clinics as clinics_service
from app.services import doctors as doctors_service
from app.services import patients as patients_service
from app.services import schedules as schedules_service


class ClinicRefs(TypedDict):
    clinic_id: int
    specialty_id: int
    department_id: int
    room_id: int
    doctor_id: int
    patient_id: int


async def make_clinic(
    session: AsyncSession,
    *,
    name: str = "Clinic One",
    timezone: str = "Europe/Warsaw",
    specialty: str = "Cardiology",
    doctor_name: str = "Jan Kowalski",
    room_code: str = "A-101",
    slot_minutes: int = 30,
    clinic_days: tuple[int, ...] = (0, 1, 2, 3, 4),
    doctor_days: tuple[int, ...] = (0,),
) -> ClinicRefs:
    clinic = await clinics_service.create_clinic(
        session, ClinicCreate(name=name, timezone=timezone)
    )
    spec = await catalog_service.create_specialty(
        session, SpecialtyCreate(clinic_id=clinic.id, name=specialty)
    )
    dept = await catalog_service.create_department(
        session, DepartmentCreate(clinic_id=clinic.id, name="General")
    )
    room = await catalog_service.create_room(
        session,
        RoomCreate(clinic_id=clinic.id, department_id=dept.id, code=room_code),
    )
    doctor = await doctors_service.create_doctor(
        session,
        DoctorCreate(
            clinic_id=clinic.id,
            full_name=doctor_name,
            specialty_id=spec.id,
            department_id=dept.id,
        ),
    )
    for weekday in clinic_days:
        await schedules_service.create_clinic_schedule(
            session,
            clinic.id,
            ClinicScheduleCreate(weekday=weekday, start_local=time(8, 0), end_local=time(20, 0)),
        )
    for weekday in doctor_days:
        await schedules_service.create_doctor_schedule(
            session,
            clinic.id,
            doctor.id,
            DoctorScheduleCreate(
                weekday=weekday,
                start_local=time(9, 0),
                end_local=time(13, 0),
                slot_minutes=slot_minutes,
                room_id=room.id,
            ),
        )
    patient = await patients_service.create_patient(
        session,
        PatientCreate(clinic_id=clinic.id, full_name="Test Patient", phone="+10000000001"),
    )
    return {
        "clinic_id": clinic.id,
        "specialty_id": spec.id,
        "department_id": dept.id,
        "room_id": room.id,
        "doctor_id": doctor.id,
        "patient_id": patient.id,
    }


def future_monday(min_days_out: int = 7) -> date:
    """A Monday far enough out for booking tests (never in the past)."""
    today = date.today()
    delta = (0 - today.weekday()) % 7 or 7
    while delta < min_days_out:
        delta += 7
    return today + timedelta(days=delta)
