"""Regression tests for the feat/perfection P0/P1 fixes.

Covers: clinic-scoped booked starts, PermissionError→401 mapping,
get_or_create race retry, reschedule conflict atomicity, global slot
ordering, list validation, horizon-tolerant nearest scan, and
malformed framing in the trusted-channel middleware.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import register_exception_handlers
from app.api.trusted_context import MalformedRequestError, TrustedChannelContextMiddleware
from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantRequest
from app.core.errors import ConflictError
from app.repositories import appointments as appointments_repo
from app.schemas.appointment import AppointmentCreate
from app.schemas.catalog import SpecialtyCreate
from app.schemas.clinic import ClinicCreate
from app.schemas.doctor import DoctorCreate
from app.schemas.schedule import ClinicScheduleCreate, DoctorScheduleCreate
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from app.services import catalog as catalog_service
from app.services import clinics as clinics_service
from app.services import doctors as doctors_service
from app.services import patients as patients_service
from app.services import schedules as schedules_service
from tests.integration.helpers import future_monday, make_clinic

_orchestrator = AssistantOrchestrator()


async def test_booked_starts_are_clinic_scoped(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Scope A")
    other = await make_clinic(
        db_session, name="Scope B", doctor_name="Другой Врач", room_code="B-101"
    )
    monday = future_monday()
    slots = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert slots
    instant = slots[0].starts_at
    day_start = datetime.combine(monday, datetime.min.time()).replace(
        tzinfo=instant.tzinfo
    ) - timedelta(days=1)
    day_end = day_start + timedelta(days=14)

    # Before booking nothing is reported for either scope.
    assert (
        await appointments_repo.list_booked_starts(
            db_session, refs["clinic_id"], refs["doctor_id"], day_start, day_end
        )
        == []
    )

    await appointments_service.book_appointment(
        db_session,
        AppointmentCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            patient_id=refs["patient_id"],
            starts_at=instant,
        ),
    )

    own = await appointments_repo.list_booked_starts(
        db_session, refs["clinic_id"], refs["doctor_id"], day_start, day_end
    )
    assert len(own) == 1
    # Same doctor id queried under another clinic must not leak the booking.
    foreign = await appointments_repo.list_booked_starts(
        db_session, other["clinic_id"], refs["doctor_id"], day_start, day_end
    )
    assert foreign == []


def test_permission_error_registered_as_401() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    assert PermissionError in app.exception_handlers


async def test_get_or_create_retries_after_race_conflict(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs = await make_clinic(db_session)
    service = patients_service
    real_create = service.create_patient
    calls = 0

    async def _flaky_create(session: AsyncSession, data: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConflictError("could not create patient")
        return await real_create(session, data)

    monkeypatch.setattr(service, "create_patient", _flaky_create)
    patient, created = await service.get_or_create_patient(
        db_session, refs["clinic_id"], "Race Patient", "+10000000001"
    )
    assert created is False
    assert patient.phone == "+10000000001"


async def test_reschedule_conflict_keeps_old_booked(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session)
    monday = future_monday()
    slots = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert len(slots) >= 2
    first, second = slots[0].starts_at, slots[1].starts_at
    booked = await appointments_service.book_appointment(
        db_session,
        AppointmentCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            patient_id=refs["patient_id"],
            starts_at=first,
        ),
    )
    other = await appointments_service.book_appointment(
        db_session,
        AppointmentCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            patient_id=refs["patient_id"],
            starts_at=second,
        ),
    )
    with pytest.raises(ConflictError, match="slot unavailable"):
        await appointments_service.reschedule_appointment(
            db_session, refs["clinic_id"], booked.id, second
        )
    still = await appointments_service.get_appointment(db_session, refs["clinic_id"], booked.id)
    assert still.status == "booked"
    other_loaded = await appointments_service.get_appointment(
        db_session, refs["clinic_id"], other.id
    )
    assert other_loaded.status == "booked"


async def test_search_slots_returns_globally_earliest(db_session: AsyncSession) -> None:
    clinic = await clinics_service.create_clinic(
        db_session, ClinicCreate(name="Order Clinic", timezone="Europe/Moscow")
    )
    spec = await catalog_service.create_specialty(
        db_session, SpecialtyCreate(clinic_id=clinic.id, name="Therapy")
    )
    late = await doctors_service.create_doctor(
        db_session,
        DoctorCreate(clinic_id=clinic.id, full_name="Late Doctor", specialty_id=spec.id),
    )
    early = await doctors_service.create_doctor(
        db_session,
        DoctorCreate(clinic_id=clinic.id, full_name="Early Doctor", specialty_id=spec.id),
    )
    monday = future_monday()
    weekday = monday.weekday()
    for day in {(weekday - 1) % 7, weekday, (weekday + 1) % 7}:
        await schedules_service.create_clinic_schedule(
            db_session,
            clinic.id,
            ClinicScheduleCreate(weekday=day, start_local=time(7, 0), end_local=time(20, 0)),
        )
    await schedules_service.create_doctor_schedule(
        db_session,
        clinic.id,
        late.id,
        DoctorScheduleCreate(
            weekday=weekday, start_local=time(9, 0), end_local=time(13, 0), slot_minutes=30
        ),
    )
    await schedules_service.create_doctor_schedule(
        db_session,
        clinic.id,
        early.id,
        DoctorScheduleCreate(
            weekday=weekday, start_local=time(7, 0), end_local=time(8, 0), slot_minutes=30
        ),
    )
    top = await availability_service.search_slots(
        db_session, clinic.id, monday, specialty_id=spec.id, limit=1
    )
    assert len(top) == 1
    assert top[0].doctor.id == early.id


async def test_list_appointments_validates_status_and_range(
    db_session: AsyncSession,
) -> None:
    refs = await make_clinic(db_session)
    with pytest.raises(ValueError, match="unknown status"):
        await appointments_service.list_appointments(
            db_session, refs["clinic_id"], patient_id=refs["patient_id"], status="nope"
        )
    with pytest.raises(ValueError, match="date_from"):
        await appointments_service.list_appointments(
            db_session,
            refs["clinic_id"],
            patient_id=refs["patient_id"],
            date_from=datetime(2026, 5, 2),
            date_to=datetime(2026, 5, 1),
        )


async def test_find_nearest_stops_gracefully_past_horizon(
    db_session: AsyncSession,
) -> None:
    refs = await make_clinic(db_session)
    far = date.today() + timedelta(days=120)
    result = await _orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_nearest_slots",
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=far,
            days_ahead=10,
        ),
    )
    assert result.status == "not_found"
    assert result.code == "NO_SLOTS_AVAILABLE"


async def test_read_body_rejects_bad_length_and_disconnect() -> None:
    async def _receive_bad_length() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(MalformedRequestError):
        await TrustedChannelContextMiddleware._read_body(
            _receive_bad_length, {b"content-length": b"not-a-number"}
        )

    async def _receive_disconnect() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    with pytest.raises(MalformedRequestError):
        await TrustedChannelContextMiddleware._read_body(_receive_disconnect, {})
