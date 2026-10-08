"""Demo dataset: four Russian clinics with catalogs, schedules and patients.

Covers Moscow, Saint Petersburg, Kazan and Novosibirsk (two time zones).
Every specialty below exists in exactly two clinics so cross-city routing
(«нет кардиолога — проверю другой филиал») stays demonstrable. The room
code «101» and the doctor name «Анна Смирнова» repeat across clinics on
purpose — they prove clinic-scoped uniqueness / separate records.

Idempotent: re-running skips clinics that already exist (matched by name).
Requires the schema to exist (run `alembic upgrade head` first).

Usage:
    docker compose exec api python -m app.db.seed_demo
"""

import asyncio
from datetime import date, time, timedelta
from typing import TypedDict
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


class _Duty(TypedDict):
    weekdays: tuple[int, ...]
    start: time
    end: time
    slot_minutes: int


# Rotated across doctors so neighbouring weekdays and several grid sizes
# are always covered.
_DUTIES: tuple[_Duty, ...] = (
    {"weekdays": (0, 2), "start": time(9, 0), "end": time(13, 0), "slot_minutes": 20},
    {"weekdays": (1, 3), "start": time(10, 0), "end": time(16, 0), "slot_minutes": 15},
    {"weekdays": (0, 2, 4), "start": time(9, 0), "end": time(14, 0), "slot_minutes": 30},
    {"weekdays": (1, 3), "start": time(10, 0), "end": time(16, 0), "slot_minutes": 30},
)


class _DoctorSpec(TypedDict):
    full_name: str
    specialty: str
    department: str
    phone: str
    room: str | None


class _ClinicSpec(TypedDict):
    name: str
    description: str
    address: str
    city: str
    latitude: float
    longitude: float
    phone: str
    timezone: str
    departments: list[tuple[str, int]]
    rooms: list[tuple[str, str, str]]
    specialties: list[str]
    doctors: list[_DoctorSpec]
    patients: list[tuple[str, str]]


_CLINICS: tuple[_ClinicSpec, ...] = (
    {
        "name": "Клиника Северная звезда",
        "description": "Demo clinic (Moscow)",
        "address": "Тверской бульвар, 12, Москва",
        "city": "Москва",
        "latitude": 55.7558,
        "longitude": 37.6173,
        "phone": "+7 495 000-00-01",
        "timezone": "Europe/Moscow",
        "departments": [
            ("Терапевтическое отделение", 1),
            ("Кардиологическое отделение", 2),
            ("Стоматология", 0),
        ],
        "rooms": [
            ("101", "Кабинет кардиолога", "Кардиологическое отделение"),
            ("102", "Кабинет стоматолога", "Стоматология"),
            ("103", "Кабинет терапевта", "Терапевтическое отделение"),
        ],
        "specialties": ["Cardiology", "Neurology", "Therapist", "Dentistry"],
        "doctors": [
            {
                "full_name": "Андрей Волков",
                "specialty": "Cardiology",
                "department": "Кардиологическое отделение",
                "phone": "+7 921 000-00-01",
                "room": "101",
            },
            {
                "full_name": "Анна Смирнова",
                "specialty": "Cardiology",
                "department": "Кардиологическое отделение",
                "phone": "+7 921 000-00-02",
                "room": "101",
            },
            {
                "full_name": "Дмитрий Орлов",
                "specialty": "Neurology",
                "department": "Терапевтическое отделение",
                "phone": "+7 921 000-00-03",
                "room": None,
            },
            {
                "full_name": "Елена Кузнецова",
                "specialty": "Neurology",
                "department": "Терапевтическое отделение",
                "phone": "+7 921 000-00-04",
                "room": None,
            },
            {
                "full_name": "Игорь Соколов",
                "specialty": "Therapist",
                "department": "Терапевтическое отделение",
                "phone": "+7 921 000-00-05",
                "room": "103",
            },
            {
                "full_name": "Мария Попова",
                "specialty": "Therapist",
                "department": "Терапевтическое отделение",
                "phone": "+7 921 000-00-06",
                "room": "103",
            },
            {
                "full_name": "Сергей Лебедев",
                "specialty": "Dentistry",
                "department": "Стоматология",
                "phone": "+7 921 000-00-07",
                "room": "102",
            },
            {
                "full_name": "Ольга Морозова",
                "specialty": "Dentistry",
                "department": "Стоматология",
                "phone": "+7 921 000-00-08",
                "room": "102",
            },
        ],
        "patients": [
            ("Демо Пациент М1", "+7 900 000-00-01"),
            ("Демо Пациент М2", "+7 900 000-00-02"),
            ("Демо Пациент М3", "+7 900 000-00-03"),
        ],
    },
    {
        "name": "Нева-Мед",
        "description": "Demo clinic (Saint Petersburg)",
        "address": "Невский проспект, 45, Санкт-Петербург",
        "city": "Санкт-Петербург",
        "latitude": 59.9343,
        "longitude": 30.3351,
        "phone": "+7 812 000-00-02",
        "timezone": "Europe/Moscow",
        "departments": [
            ("Детское отделение", 2),
            ("Диагностика", 0),
            ("Хирургия", 1),
        ],
        "rooms": [
            ("101", "Кабинет педиатра", "Детское отделение"),
            ("102", "Кабинет дерматолога", "Диагностика"),
            ("103", "Кабинет хирурга", "Хирургия"),
        ],
        "specialties": ["Pediatrics", "Dermatology", "Ophthalmology", "Surgery"],
        "doctors": [
            {
                "full_name": "Наталья Белова",
                "specialty": "Pediatrics",
                "department": "Детское отделение",
                "phone": "+7 921 000-01-01",
                "room": "101",
            },
            {
                "full_name": "Павел Фёдоров",
                "specialty": "Pediatrics",
                "department": "Детское отделение",
                "phone": "+7 921 000-01-02",
                "room": "101",
            },
            {
                "full_name": "Татьяна Громова",
                "specialty": "Dermatology",
                "department": "Диагностика",
                "phone": "+7 921 000-01-03",
                "room": "102",
            },
            {
                "full_name": "Виктор Захаров",
                "specialty": "Dermatology",
                "department": "Диагностика",
                "phone": "+7 921 000-01-04",
                "room": "102",
            },
            {
                "full_name": "Ирина Лазарева",
                "specialty": "Ophthalmology",
                "department": "Диагностика",
                "phone": "+7 921 000-01-05",
                "room": None,
            },
            {
                "full_name": "Константин Рябов",
                "specialty": "Ophthalmology",
                "department": "Диагностика",
                "phone": "+7 921 000-01-06",
                "room": None,
            },
            {
                "full_name": "Александр Тихонов",
                "specialty": "Surgery",
                "department": "Хирургия",
                "phone": "+7 921 000-01-07",
                "room": "103",
            },
            {
                "full_name": "Светлана Ершова",
                "specialty": "Surgery",
                "department": "Хирургия",
                "phone": "+7 921 000-01-08",
                "room": "103",
            },
        ],
        "patients": [
            ("Демо Пациент С1", "+7 900 000-01-01"),
            ("Демо Пациент С2", "+7 900 000-01-02"),
            ("Демо Пациент С3", "+7 900 000-01-03"),
        ],
    },
    {
        "name": "Волга Плюс",
        "description": "Demo clinic (Kazan)",
        "address": "ул. Баумана, 20, Казань",
        "city": "Казань",
        "latitude": 55.7887,
        "longitude": 49.1221,
        "phone": "+7 843 000-00-03",
        "timezone": "Europe/Moscow",
        "departments": [
            ("Терапевтическое отделение", 1),
            ("Детское отделение", 2),
            ("ЛОР-кабинет", 0),
        ],
        "rooms": [
            ("101", "Кабинет кардиолога", "Терапевтическое отделение"),
            ("102", "Кабинет ЛОРа", "ЛОР-кабинет"),
            ("103", "Кабинет педиатра", "Детское отделение"),
        ],
        "specialties": ["Cardiology", "Pediatrics", "Otolaryngology", "Therapist"],
        "doctors": [
            {
                "full_name": "Марат Гареев",
                "specialty": "Cardiology",
                "department": "Терапевтическое отделение",
                "phone": "+7 917 000-02-01",
                "room": "101",
            },
            {
                # Same name as in Moscow on purpose — separate per-clinic rows.
                "full_name": "Анна Смирнова",
                "specialty": "Cardiology",
                "department": "Терапевтическое отделение",
                "phone": "+7 917 000-02-02",
                "room": "101",
            },
            {
                "full_name": "Алина Хабибуллина",
                "specialty": "Pediatrics",
                "department": "Детское отделение",
                "phone": "+7 917 000-02-03",
                "room": "103",
            },
            {
                "full_name": "Руслан Сафин",
                "specialty": "Pediatrics",
                "department": "Детское отделение",
                "phone": "+7 917 000-02-04",
                "room": "103",
            },
            {
                "full_name": "Олег Карпов",
                "specialty": "Otolaryngology",
                "department": "ЛОР-кабинет",
                "phone": "+7 917 000-02-05",
                "room": "102",
            },
            {
                "full_name": "Динара Юсупова",
                "specialty": "Otolaryngology",
                "department": "ЛОР-кабинет",
                "phone": "+7 917 000-02-06",
                "room": "102",
            },
            {
                "full_name": "Надежда Воронова",
                "specialty": "Therapist",
                "department": "Терапевтическое отделение",
                "phone": "+7 917 000-02-07",
                "room": None,
            },
            {
                "full_name": "Тимур Ахметов",
                "specialty": "Therapist",
                "department": "Терапевтическое отделение",
                "phone": "+7 917 000-02-08",
                "room": None,
            },
        ],
        "patients": [
            ("Демо Пациент К1", "+7 900 000-02-01"),
            ("Демо Пациент К2", "+7 900 000-02-02"),
            ("Демо Пациент К3", "+7 900 000-02-03"),
        ],
    },
    {
        "name": "Сибирь Здоровье",
        "description": "Demo clinic (Novosibirsk)",
        "address": "Красный проспект, 77, Новосибирск",
        "city": "Новосибирск",
        "latitude": 55.0084,
        "longitude": 82.9357,
        "phone": "+7 383 000-00-04",
        "timezone": "Asia/Novosibirsk",
        "departments": [
            ("Неврологическое отделение", 2),
            ("Хирургия", 1),
            ("Диагностика", 0),
        ],
        "rooms": [
            ("101", "Кабинет невролога", "Неврологическое отделение"),
            ("102", "Кабинет дерматолога", "Диагностика"),
            ("103", "Кабинет хирурга", "Хирургия"),
        ],
        "specialties": ["Neurology", "Surgery", "Dentistry", "Dermatology"],
        "doctors": [
            {
                "full_name": "Владимир Ким",
                "specialty": "Neurology",
                "department": "Неврологическое отделение",
                "phone": "+7 913 000-03-01",
                "room": "101",
            },
            {
                "full_name": "Оксана Пак",
                "specialty": "Neurology",
                "department": "Неврологическое отделение",
                "phone": "+7 913 000-03-02",
                "room": "101",
            },
            {
                "full_name": "Борис Мельников",
                "specialty": "Surgery",
                "department": "Хирургия",
                "phone": "+7 913 000-03-03",
                "room": "103",
            },
            {
                "full_name": "Жанна Фролова",
                "specialty": "Surgery",
                "department": "Хирургия",
                "phone": "+7 913 000-03-04",
                "room": "103",
            },
            {
                "full_name": "Денис Щербаков",
                "specialty": "Dentistry",
                "department": "Диагностика",
                "phone": "+7 913 000-03-05",
                "room": None,
            },
            {
                "full_name": "Любовь Анисимова",
                "specialty": "Dentistry",
                "department": "Диагностика",
                "phone": "+7 913 000-03-06",
                "room": None,
            },
            {
                "full_name": "Григорий Панкратов",
                "specialty": "Dermatology",
                "department": "Диагностика",
                "phone": "+7 913 000-03-07",
                "room": "102",
            },
            {
                "full_name": "Вера Логинова",
                "specialty": "Dermatology",
                "department": "Диагностика",
                "phone": "+7 913 000-03-08",
                "room": "102",
            },
        ],
        "patients": [
            ("Демо Пациент Н1", "+7 900 000-03-01"),
            ("Демо Пациент Н2", "+7 900 000-03-02"),
            ("Демо Пациент Н3", "+7 900 000-03-03"),
        ],
    },
)


def _next_weekday(from_date: date, weekday: int) -> date:
    """Next date with the given weekday strictly after from_date."""
    delta = (weekday - from_date.weekday()) % 7 or 7
    return from_date + timedelta(days=delta)


async def _seed_clinic(session: AsyncSession, spec: _ClinicSpec) -> dict[str, int]:
    clinic = await clinics_service.create_clinic(
        session,
        ClinicCreate(
            name=spec["name"],
            description=spec["description"],
            address=spec["address"],
            city=spec["city"],
            latitude=spec["latitude"],
            longitude=spec["longitude"],
            phone=spec["phone"],
            timezone=spec["timezone"],
        ),
    )
    specialty_ids: dict[str, int] = {}
    for spec_name in spec["specialties"]:
        specialty = await catalog_service.create_specialty(
            session, SpecialtyCreate(clinic_id=clinic.id, name=spec_name)
        )
        specialty_ids[spec_name.lower()] = specialty.id
    department_ids: dict[str, int] = {}
    for dept_name, floor in spec["departments"]:
        department = await catalog_service.create_department(
            session, DepartmentCreate(clinic_id=clinic.id, name=dept_name, floor=floor)
        )
        department_ids[dept_name] = department.id
    room_ids: dict[str, int] = {}
    for code, label, room_dept in spec["rooms"]:
        room = await catalog_service.create_room(
            session,
            RoomCreate(
                clinic_id=clinic.id,
                department_id=department_ids[room_dept],
                code=code,
                label=label,
            ),
        )
        room_ids[code] = room.id
    # Clinic week Mon-Sat 08:00-20:00.
    for weekday in (0, 1, 2, 3, 4, 5):
        await schedules_service.create_clinic_schedule(
            session,
            clinic.id,
            ClinicScheduleCreate(weekday=weekday, start_local=time(8, 0), end_local=time(20, 0)),
        )
    first_doctor_id: int | None = None
    for index, doctor in enumerate(spec["doctors"]):
        doctor_row = await doctors_service.create_doctor(
            session,
            DoctorCreate(
                clinic_id=clinic.id,
                full_name=doctor["full_name"],
                specialty_id=specialty_ids[doctor["specialty"].lower()],
                department_id=department_ids[doctor["department"]],
                phone=doctor["phone"],
            ),
        )
        if first_doctor_id is None:
            first_doctor_id = doctor_row.id
        duty = _DUTIES[index % len(_DUTIES)]
        for weekday in duty["weekdays"]:
            await schedules_service.create_doctor_schedule(
                session,
                clinic.id,
                doctor_row.id,
                DoctorScheduleCreate(
                    weekday=weekday,
                    start_local=duty["start"],
                    end_local=duty["end"],
                    slot_minutes=duty["slot_minutes"],
                    room_id=room_ids[doctor["room"]] if doctor["room"] else None,
                ),
            )
    # Clinic holiday 12 days out (dynamic so the demo never rots).
    await exceptions_service.create_exception(
        session,
        ScheduleExceptionCreate(
            clinic_id=clinic.id,
            date=date.today() + timedelta(days=12),
            kind="day_off",
            reason="Санитарный день",
        ),
    )
    first_patient_id: int | None = None
    for full_name, phone in spec["patients"]:
        patient = await patients_service.create_patient(
            session, PatientCreate(clinic_id=clinic.id, full_name=full_name, phone=phone)
        )
        if first_patient_id is None:
            first_patient_id = patient.id
    assert first_doctor_id is not None and first_patient_id is not None
    return {"clinic_id": clinic.id, "doctor_id": first_doctor_id, "patient_id": first_patient_id}


async def _book_demo_appointment(
    session: AsyncSession, clinic_id: int, doctor_id: int, patient_id: int
) -> None:
    """Book the first free slot of the clinic's first doctor on the next Monday."""
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
        first_refs: dict[str, int] | None = None
        for spec in _CLINICS:
            if await _clinic_exists(session, spec["name"]):
                print(f"seed: {spec['name']} exists, skipping")
                continue
            try:
                refs = await _seed_clinic(session, spec)
            except ConflictError as exc:
                print(f"seed: {spec['name']} conflict ({exc.message}), skipping")
                continue
            print(f"seed: {spec['name']} created id={refs['clinic_id']}")
            if first_refs is None:
                first_refs = refs
        if first_refs is not None:
            await _book_demo_appointment(
                session, first_refs["clinic_id"], first_refs["doctor_id"], first_refs["patient_id"]
            )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
