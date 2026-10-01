"""Multi-tenancy: Clinic A data is invisible under Clinic B scope."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.schemas.appointment import AppointmentCreate
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from app.services import catalog as catalog_service
from app.services import doctors as doctors_service
from app.services import patients as patients_service
from tests.integration.helpers import future_monday, make_clinic


async def test_cross_clinic_access_returns_404(db_session: AsyncSession) -> None:
    clinic_a = await make_clinic(
        db_session, name="Isolation A", doctor_name="Jan Kowalski", room_code="A-101"
    )
    clinic_b = await make_clinic(
        db_session, name="Isolation B", doctor_name="Jan Kowalski", room_code="A-101"
    )
    assert clinic_a["doctor_id"] != clinic_b["doctor_id"]

    # Same names/codes, different records.
    with pytest.raises(NotFoundError):
        await doctors_service.get_doctor(db_session, clinic_b["clinic_id"], clinic_a["doctor_id"])
    with pytest.raises(NotFoundError):
        await patients_service.get_patient(
            db_session, clinic_b["clinic_id"], clinic_a["patient_id"]
        )
    with pytest.raises(NotFoundError):
        await catalog_service.get_room(db_session, clinic_b["clinic_id"], clinic_a["room_id"])

    # Doctor listing is scoped: B never sees A's doctor.
    doctors_b = await doctors_service.list_doctors(db_session, clinic_b["clinic_id"])
    assert {d.id for d in doctors_b} == {clinic_b["doctor_id"]}


async def test_appointment_requires_same_clinic_scope(
    db_session: AsyncSession,
) -> None:
    clinic_a = await make_clinic(db_session, name="Scope A")
    clinic_b = await make_clinic(db_session, name="Scope B")

    # A's appointment is not visible under B.
    monday = future_monday()
    slots = await availability_service.search_slots(
        db_session, clinic_a["clinic_id"], monday, doctor_id=clinic_a["doctor_id"]
    )
    booked = await appointments_service.book_appointment(
        db_session,
        AppointmentCreate(
            clinic_id=clinic_a["clinic_id"],
            doctor_id=clinic_a["doctor_id"],
            patient_id=clinic_a["patient_id"],
            starts_at=slots[0].starts_at,
        ),
    )
    with pytest.raises(NotFoundError):
        await appointments_service.get_appointment(db_session, clinic_b["clinic_id"], booked.id)
    listed = await appointments_service.list_appointments(
        db_session, clinic_b["clinic_id"], doctor_id=clinic_b["doctor_id"]
    )
    assert listed == []
