"""Cross-tenant writes: every FK is scope-checked, misses → 404."""

from datetime import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.schemas.appointment import AppointmentCreate
from app.schemas.catalog import RoomCreate
from app.schemas.doctor import DoctorCreate, DoctorUpdate
from app.schemas.schedule import DoctorScheduleCreate, ScheduleExceptionCreate
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from app.services import catalog as catalog_service
from app.services import doctors as doctors_service
from app.services import exceptions as exceptions_service
from app.services import schedules as schedules_service
from tests.integration.helpers import future_monday, make_clinic


async def test_cross_tenant_writes_return_404(db_session: AsyncSession) -> None:
    clinic_a = await make_clinic(db_session, name="Write A")
    clinic_b = await make_clinic(db_session, name="Write B")
    aid, bid = clinic_a["clinic_id"], clinic_b["clinic_id"]

    # Doctor with another clinic's specialty.
    with pytest.raises(NotFoundError):
        await doctors_service.create_doctor(
            db_session,
            DoctorCreate(
                clinic_id=bid,
                full_name="Dr. Cross",
                specialty_id=clinic_a["specialty_id"],
            ),
        )
    # Doctor update pointing at another clinic's specialty.
    with pytest.raises(NotFoundError):
        await doctors_service.update_doctor(
            db_session,
            bid,
            clinic_b["doctor_id"],
            DoctorUpdate(specialty_id=clinic_a["specialty_id"]),
        )
    # Room with another clinic's department.
    with pytest.raises(NotFoundError):
        await catalog_service.create_room(
            db_session,
            RoomCreate(
                clinic_id=bid,
                department_id=clinic_a["department_id"],
                code="X-1",
            ),
        )
    # Doctor schedule with another clinic's room.
    with pytest.raises(NotFoundError):
        await schedules_service.create_doctor_schedule(
            db_session,
            bid,
            clinic_b["doctor_id"],
            DoctorScheduleCreate(
                weekday=5,
                start_local=time(9, 0),
                end_local=time(10, 0),
                room_id=clinic_a["room_id"],
            ),
        )
    # Exception for another clinic's doctor.
    with pytest.raises(NotFoundError):
        await exceptions_service.create_exception(
            db_session,
            ScheduleExceptionCreate(
                clinic_id=bid,
                doctor_id=clinic_a["doctor_id"],
                date=future_monday(),
                kind="day_off",
            ),
        )
    # Booking mixing clinics: A's patient with B's doctor.
    monday = future_monday()
    slots = await availability_service.search_slots(
        db_session, bid, monday, doctor_id=clinic_b["doctor_id"]
    )
    assert slots
    with pytest.raises(NotFoundError):
        await appointments_service.book_appointment(
            db_session,
            AppointmentCreate(
                clinic_id=bid,
                doctor_id=clinic_b["doctor_id"],
                patient_id=clinic_a["patient_id"],
                starts_at=slots[0].starts_at,
            ),
        )
    # Booking into the wrong clinic scope entirely.
    with pytest.raises(NotFoundError):
        await appointments_service.book_appointment(
            db_session,
            AppointmentCreate(
                clinic_id=aid,
                doctor_id=clinic_b["doctor_id"],
                patient_id=clinic_b["patient_id"],
                starts_at=slots[0].starts_at,
            ),
        )
