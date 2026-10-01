"""Management PATCH coverage: schedules and exceptions can be updated."""

from datetime import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.schemas.schedule import (
    ClinicScheduleUpdate,
    DoctorScheduleUpdate,
    ScheduleExceptionCreate,
    ScheduleExceptionUpdate,
)
from app.services import exceptions as exceptions_service
from app.services import schedules as schedules_service
from tests.integration.helpers import future_monday, make_clinic


async def test_update_clinic_schedule(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Upd Clinic")
    rows = await schedules_service.list_clinic_schedules(db_session, refs["clinic_id"])
    monday = next(r for r in rows if r.weekday == 0)
    updated = await schedules_service.update_clinic_schedule(
        db_session,
        refs["clinic_id"],
        monday.id,
        ClinicScheduleUpdate(start_local=time(9, 0), end_local=time(18, 0)),
    )
    assert (updated.start_local, updated.end_local) == (time(9, 0), time(18, 0))

    # Bad interval → 422-style ValueError.
    with pytest.raises(ValueError):
        await schedules_service.update_clinic_schedule(
            db_session,
            refs["clinic_id"],
            monday.id,
            ClinicScheduleUpdate(start_local=time(18, 0), end_local=time(9, 0)),
        )

    # Cross-tenant scope → 404.
    other = await make_clinic(db_session, name="Upd Other")
    with pytest.raises(NotFoundError):
        await schedules_service.update_clinic_schedule(
            db_session, other["clinic_id"], monday.id, ClinicScheduleUpdate(active=False)
        )


async def test_update_doctor_schedule_overlap_guarded(
    db_session: AsyncSession,
) -> None:
    refs = await make_clinic(db_session, name="Upd Doc")
    rows = await schedules_service.list_doctor_schedules(
        db_session, refs["clinic_id"], refs["doctor_id"]
    )
    assert len(rows) == 1
    # Deactivate then verify it disappears from default listing but is
    # still visible with active_only=False.
    await schedules_service.update_doctor_schedule(
        db_session,
        refs["clinic_id"],
        rows[0].id,
        DoctorScheduleUpdate(active=False),
    )
    assert (
        await schedules_service.list_doctor_schedules(
            db_session, refs["clinic_id"], refs["doctor_id"]
        )
        == []
    )
    all_rows = await schedules_service.list_doctor_schedules(
        db_session, refs["clinic_id"], refs["doctor_id"], active_only=False
    )
    assert len(all_rows) == 1 and not all_rows[0].active


async def test_update_exception_kind_switch(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Upd Exc")
    monday = future_monday()
    exc = await exceptions_service.create_exception(
        db_session,
        ScheduleExceptionCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
            kind="custom_hours",
            start_local=time(12, 0),
            end_local=time(14, 0),
        ),
    )
    # Switch to day_off clears times.
    updated = await exceptions_service.update_exception(
        db_session,
        refs["clinic_id"],
        exc.id,
        ScheduleExceptionUpdate(kind="day_off"),
    )
    assert updated.kind == "day_off"
    assert updated.start_local is None and updated.end_local is None

    # Switching back without times → ValueError.
    with pytest.raises(ValueError):
        await exceptions_service.update_exception(
            db_session,
            refs["clinic_id"],
            exc.id,
            ScheduleExceptionUpdate(kind="custom_hours"),
        )
    # With times → ok.
    back = await exceptions_service.update_exception(
        db_session,
        refs["clinic_id"],
        exc.id,
        ScheduleExceptionUpdate(
            kind="custom_hours", start_local=time(10, 0), end_local=time(12, 0)
        ),
    )
    assert (back.start_local, back.end_local) == (time(10, 0), time(12, 0))

    # Cross-tenant → 404.
    other = await make_clinic(db_session, name="Upd Exc Other")
    with pytest.raises(NotFoundError):
        await exceptions_service.update_exception(
            db_session,
            other["clinic_id"],
            exc.id,
            ScheduleExceptionUpdate(reason="x"),
        )


async def test_update_doctor_schedule_slot_consistency(
    db_session: AsyncSession,
) -> None:
    """Two Monday intervals must share slot_minutes; update enforces it."""
    from app.schemas.schedule import DoctorScheduleCreate

    refs = await make_clinic(db_session, name="Slot Consistency")
    rows = await schedules_service.list_doctor_schedules(
        db_session, refs["clinic_id"], refs["doctor_id"]
    )
    # Existing Monday row is 09:00-13:00 @30; shrink it, add afternoon row.
    await schedules_service.update_doctor_schedule(
        db_session,
        refs["clinic_id"],
        rows[0].id,
        DoctorScheduleUpdate(end_local=time(11, 0)),
    )
    second = await schedules_service.create_doctor_schedule(
        db_session,
        refs["clinic_id"],
        refs["doctor_id"],
        DoctorScheduleCreate(
            weekday=0, start_local=time(12, 0), end_local=time(14, 0), slot_minutes=30
        ),
    )
    with pytest.raises(ConflictError, match="slot_minutes"):
        await schedules_service.update_doctor_schedule(
            db_session,
            refs["clinic_id"],
            second.id,
            DoctorScheduleUpdate(slot_minutes=15),
        )
