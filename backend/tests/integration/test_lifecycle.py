"""Appointment lifecycle: book → duplicate 409 → reschedule → cancel → rebook."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import ConflictError
from app.schemas.appointment import AppointmentCreate
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from tests.integration.helpers import future_monday, make_clinic


async def test_full_lifecycle(db_session: AsyncSession) -> None:
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
    assert booked.status == "booked"

    # Duplicate booking of the same instant → 409.
    with pytest.raises(ConflictError, match="slot unavailable"):
        await appointments_service.book_appointment(
            db_session,
            AppointmentCreate(
                clinic_id=refs["clinic_id"],
                doctor_id=refs["doctor_id"],
                patient_id=refs["patient_id"],
                starts_at=first,
            ),
        )

    # Reschedule to the second slot; old booking becomes cancelled.
    moved = await appointments_service.reschedule_appointment(
        db_session, refs["clinic_id"], booked.id, second
    )
    assert moved.starts_at == second
    assert moved.status == "booked"
    old = await appointments_service.get_appointment(db_session, refs["clinic_id"], booked.id)
    assert old.status == "cancelled"

    # Rescheduling a cancelled appointment → 409.
    with pytest.raises(ConflictError, match="already cancelled"):
        await appointments_service.reschedule_appointment(
            db_session, refs["clinic_id"], booked.id, first
        )

    # Cancel the moved booking, then re-book the freed original slot.
    await appointments_service.cancel_appointment(db_session, refs["clinic_id"], moved.id)
    with pytest.raises(ConflictError, match="already cancelled"):
        await appointments_service.cancel_appointment(db_session, refs["clinic_id"], moved.id)
    rebooked = await appointments_service.book_appointment(
        db_session,
        AppointmentCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            patient_id=refs["patient_id"],
            starts_at=first,
        ),
    )
    assert rebooked.status == "booked"

    # Complete the booking; terminal states are immutable.
    done = await appointments_service.complete_appointment(
        db_session, refs["clinic_id"], rebooked.id
    )
    assert done.status == "completed"
    with pytest.raises(ConflictError, match="already completed"):
        await appointments_service.cancel_appointment(db_session, refs["clinic_id"], rebooked.id)


async def test_concurrent_double_booking_one_wins(db_session: AsyncSession, tmp_path: Path) -> None:
    """Two concurrent INSERTs for one instant: exactly one 201, one 409.

    Uses two independent sessions on the same file DB; SQLite serializes
    writers (busy timeout) so the loser hits the partial unique index.
    """
    refs = await make_clinic(db_session, name="Race Clinic")
    monday = future_monday()
    slots = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert slots
    instant = slots[0].starts_at

    url = f"sqlite+aiosqlite:///{tmp_path}/test.db?timeout=30"
    engine = create_async_engine(url, connect_args={"timeout": 30})
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _try_book() -> str:
        async with factory() as session:
            try:
                await appointments_service.book_appointment(
                    session,
                    AppointmentCreate(
                        clinic_id=refs["clinic_id"],
                        doctor_id=refs["doctor_id"],
                        patient_id=refs["patient_id"],
                        starts_at=instant,
                    ),
                )
            except ConflictError:
                return "conflict"
            return "booked"

    results = await asyncio.gather(_try_book(), _try_book())
    await engine.dispose()
    assert sorted(results) == ["booked", "conflict"]
