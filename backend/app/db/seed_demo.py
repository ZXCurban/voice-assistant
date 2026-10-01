"""Demo dataset: two isolated clinics with catalogs, schedules and patients.

Idempotent: re-running skips clinics that already exist (matched by name).
Requires the schema to exist (run `alembic upgrade head` first).

Usage:
    docker compose exec api python -m app.db.seed_demo
"""

import asyncio
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError
from app.db.session import get_engine
from app.models.clinic import Clinic
from app.schemas.appointment import AppointmentCreate
from app.schemas.catalog import DepartmentCreate, RoomCreate, SpecialtyCreate
from app.schemas.clinic import ClinicCreate
from app.schemas.doctor import DoctorCreate
from app.schemas.patient import PatientCreate
from app.schemas.schedule import (
    ClinicScheduleCreate,
    DoctorScheduleCreate,
    ScheduleExceptionCreate,
)
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from app.services import catalog as catalog_service
from app.services import clinics as clinics_service
from app.services import doctors as doctors_service
from app.services import exceptions as exceptions_service
from app.services import patients as patients_service
from app.services import schedules as schedules_service


def _next_weekday(from_date: date, weekday: int) -> date:
    """Next date with the given weekday strictly after from_date."""
    delta = (weekday - from_date.weekday()) % 7 or 7
    return from_date + timedelta(days=delta)


async def _seed_clinic_a(session: AsyncSession) -> dict[str, int]:
    clinic = await clinics_service.create_clinic(
        session,
        ClinicCreate(
            name="Przychodnia Srodmiescie",
            description="Demo clinic A (Warsaw)",
            address="Marszalkowska 1, Warszawa",
            phone="+48 22 000 00 01",
            timezone="Europe/Warsaw",
        ),
    )
    cardio = await catalog_service.create_specialty(
        session, SpecialtyCreate(clinic_id=clinic.id, name="Cardiology")
    )
    derm = await catalog_service.create_specialty(
        session, SpecialtyCreate(clinic_id=clinic.id, name="Dermatology")
    )
    internal = await catalog_service.create_department(
        session,
        DepartmentCreate(clinic_id=clinic.id, name="Internal Medicine", floor=1),
    )
    outpatient = await catalog_service.create_department(
        session,
        DepartmentCreate(clinic_id=clinic.id, name="Outpatient Department", floor=0),
    )
    room_a101 = await catalog_service.create_room(
        session,
        RoomCreate(
            clinic_id=clinic.id,
            department_id=internal.id,
            code="A-101",
            label="Cardiology office",
            floor=1,
        ),
    )
    await catalog_service.create_room(
        session,
        RoomCreate(
            clinic_id=clinic.id,
            department_id=outpatient.id,
            code="A-102",
            label="Dermatology office",
            floor=0,
        ),
    )
    kowalski = await doctors_service.create_doctor(
        session,
        DoctorCreate(
            clinic_id=clinic.id,
            full_name="Jan Kowalski",
            specialty_id=cardio.id,
            department_id=internal.id,
            phone="+48 600 000 001",
        ),
    )
    nowak = await doctors_service.create_doctor(
        session,
        DoctorCreate(
            clinic_id=clinic.id,
            full_name="Anna Nowak",
            specialty_id=derm.id,
            department_id=outpatient.id,
            phone="+48 600 000 002",
        ),
    )
    for weekday in (0, 1, 2, 3, 4):  # Mon-Fri 08:00-20:00
        await schedules_service.create_clinic_schedule(
            session,
            clinic.id,
            ClinicScheduleCreate(weekday=weekday, start_local=time(8, 0), end_local=time(20, 0)),
        )
    for weekday in (0, 2):  # Mon + Wed 09:00-13:00, 20-min grid
        await schedules_service.create_doctor_schedule(
            session,
            clinic.id,
            kowalski.id,
            DoctorScheduleCreate(
                weekday=weekday,
                start_local=time(9, 0),
                end_local=time(13, 0),
                slot_minutes=20,
                room_id=room_a101.id,
            ),
        )
    for weekday in (1, 3):  # Tue + Thu 10:00-16:00, 15-min grid
        await schedules_service.create_doctor_schedule(
            session,
            clinic.id,
            nowak.id,
            DoctorScheduleCreate(
                weekday=weekday,
                start_local=time(10, 0),
                end_local=time(16, 0),
                slot_minutes=15,
            ),
        )
    # Clinic holiday 12 days out (dynamic so the demo never rots).
    holiday = date.today() + timedelta(days=12)
    await exceptions_service.create_exception(
        session,
        ScheduleExceptionCreate(
            clinic_id=clinic.id, date=holiday, kind="day_off", reason="Demo holiday"
        ),
    )
    pat1 = await patients_service.create_patient(
        session,
        PatientCreate(clinic_id=clinic.id, full_name="Demo Pacjent A1", phone="+48 700 000 001"),
    )
    await patients_service.create_patient(
        session,
        PatientCreate(clinic_id=clinic.id, full_name="Demo Pacjent A2", phone="+48 700 000 002"),
    )
    return {"clinic_id": clinic.id, "doctor_id": kowalski.id, "patient_id": pat1.id}


async def _seed_clinic_b(session: AsyncSession) -> int:
    clinic = await clinics_service.create_clinic(
        session,
        ClinicCreate(
            name="Clinica Atlantica",
            description="Demo clinic B (Lisbon)",
            address="Av. Atlantica 10, Lisboa",
            phone="+351 21 000 00 02",
            timezone="Europe/Lisbon",
        ),
    )
    ped = await catalog_service.create_specialty(
        session, SpecialtyCreate(clinic_id=clinic.id, name="Pediatrics")
    )
    neuro = await catalog_service.create_specialty(
        session, SpecialtyCreate(clinic_id=clinic.id, name="Neurology")
    )
    ped_dept = await catalog_service.create_department(
        session, DepartmentCreate(clinic_id=clinic.id, name="Pediatrics", floor=2)
    )
    diag_dept = await catalog_service.create_department(
        session, DepartmentCreate(clinic_id=clinic.id, name="Diagnostics", floor=0)
    )
    # Same room code as clinic A on purpose — proves clinic-scoped uniqueness.
    await catalog_service.create_room(
        session,
        RoomCreate(
            clinic_id=clinic.id,
            department_id=ped_dept.id,
            code="A-101",
            label="Pediatrics office",
            floor=2,
        ),
    )
    room_b201 = await catalog_service.create_room(
        session,
        RoomCreate(
            clinic_id=clinic.id,
            department_id=diag_dept.id,
            code="B-201",
            label="Neurology office",
            floor=0,
        ),
    )
    # Same doctor name as clinic A on purpose — proves separate records.
    kowalski_b = await doctors_service.create_doctor(
        session,
        DoctorCreate(
            clinic_id=clinic.id,
            full_name="Jan Kowalski",
            specialty_id=ped.id,
            department_id=ped_dept.id,
        ),
    )
    silva = await doctors_service.create_doctor(
        session,
        DoctorCreate(
            clinic_id=clinic.id,
            full_name="Maria Silva",
            specialty_id=neuro.id,
            department_id=diag_dept.id,
        ),
    )
    for weekday in (0, 1, 2, 3, 4, 5):  # Mon-Sat 09:00-18:00
        await schedules_service.create_clinic_schedule(
            session,
            clinic.id,
            ClinicScheduleCreate(weekday=weekday, start_local=time(9, 0), end_local=time(18, 0)),
        )
    for weekday in (0, 2, 4):
        await schedules_service.create_doctor_schedule(
            session,
            clinic.id,
            kowalski_b.id,
            DoctorScheduleCreate(
                weekday=weekday,
                start_local=time(9, 0),
                end_local=time(14, 0),
                slot_minutes=30,
            ),
        )
    for weekday in (1, 3):
        await schedules_service.create_doctor_schedule(
            session,
            clinic.id,
            silva.id,
            DoctorScheduleCreate(
                weekday=weekday,
                start_local=time(10, 0),
                end_local=time(16, 0),
                slot_minutes=30,
                room_id=room_b201.id,
            ),
        )
    # Doctor day off + custom hours (both override kinds demoed).
    await exceptions_service.create_exception(
        session,
        ScheduleExceptionCreate(
            clinic_id=clinic.id,
            doctor_id=silva.id,
            date=date.today() + timedelta(days=12),
            kind="day_off",
            reason="Conference",
        ),
    )
    await exceptions_service.create_exception(
        session,
        ScheduleExceptionCreate(
            clinic_id=clinic.id,
            doctor_id=silva.id,
            date=date.today() + timedelta(days=13),
            kind="custom_hours",
            start_local=time(12, 0),
            end_local=time(18, 0),
            reason="Short day",
        ),
    )
    # Same patient names as clinic A — separate per-clinic rows.
    await patients_service.create_patient(
        session,
        PatientCreate(clinic_id=clinic.id, full_name="Demo Pacjent A1", phone="+351 900 000 001"),
    )
    await patients_service.create_patient(
        session,
        PatientCreate(clinic_id=clinic.id, full_name="Demo Pacjent A2", phone="+351 900 000 002"),
    )
    return clinic.id


async def _book_demo_appointment(
    session: AsyncSession, clinic_id: int, doctor_id: int, patient_id: int
) -> None:
    """Book the first free slot of clinic A's doctor on the next Monday."""
    clinic = await clinics_service.get_clinic(session, clinic_id)
    tz = ZoneInfo(clinic.timezone)
    target = _next_weekday(date.today(), 0)
    slots = await availability_service.search_slots(session, clinic_id, target, doctor_id=doctor_id)
    if not slots:
        print(f"seed: no free slots on {target} (holiday?), skipping demo booking")
        return
    appointment = await appointments_service.book_appointment(
        session,
        AppointmentCreate(
            clinic_id=clinic_id,
            doctor_id=doctor_id,
            patient_id=patient_id,
            starts_at=slots[0].starts_at,
        ),
    )
    local = appointment.starts_at.astimezone(tz)
    print(f"seed: demo booking id={appointment.id} at {local:%Y-%m-%d %H:%M %Z}")


async def _clinic_exists(session: AsyncSession, name: str) -> bool:
    result = await session.execute(select(Clinic.id).where(Clinic.name == name))
    return result.scalar_one_or_none() is not None


async def main() -> None:
    engine = get_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        refs: dict[str, int] | None = None
        if await _clinic_exists(session, "Przychodnia Srodmiescie"):
            print("seed: clinic A exists, skipping")
        else:
            try:
                refs = await _seed_clinic_a(session)
            except ConflictError as exc:
                print(f"seed: clinic A conflict ({exc.message}), skipping")
            else:
                print(f"seed: clinic A created id={refs['clinic_id']}")
        if await _clinic_exists(session, "Clinica Atlantica"):
            print("seed: clinic B exists, skipping")
        else:
            try:
                clinic_b_id = await _seed_clinic_b(session)
            except ConflictError as exc:
                print(f"seed: clinic B conflict ({exc.message}), skipping")
            else:
                print(f"seed: clinic B created id={clinic_b_id}")
        if refs is not None:
            await _book_demo_appointment(
                session, refs["clinic_id"], refs["doctor_id"], refs["patient_id"]
            )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
