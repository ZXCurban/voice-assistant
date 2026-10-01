"""Schedule exceptions: day off hides slots, custom hours replace them."""

from datetime import time
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.schedule import ScheduleExceptionCreate
from app.services import availability as availability_service
from app.services import exceptions as exceptions_service
from tests.integration.helpers import future_monday, make_clinic


async def test_day_off_hides_and_restores_slots(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Exception Clinic")
    monday = future_monday()
    before = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert len(before) == 8  # 09:00-13:00 @ 30 min

    exc = await exceptions_service.create_exception(
        db_session,
        ScheduleExceptionCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
            kind="day_off",
        ),
    )
    during = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert during == []

    await exceptions_service.delete_exception(db_session, refs["clinic_id"], exc.id)
    after = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert len(after) == 8


async def test_clinic_holiday_hides_all_doctors(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Holiday Clinic")
    monday = future_monday()
    await exceptions_service.create_exception(
        db_session,
        ScheduleExceptionCreate(clinic_id=refs["clinic_id"], date=monday, kind="day_off"),
    )
    slots = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    assert slots == []


async def test_custom_hours_replace_weekly_template(
    db_session: AsyncSession,
) -> None:
    refs = await make_clinic(db_session, name="Custom Clinic")
    monday = future_monday()
    await exceptions_service.create_exception(
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
    slots = await availability_service.search_slots(
        db_session, refs["clinic_id"], monday, doctor_id=refs["doctor_id"]
    )
    # 12:00-14:00 @ 30 min → 4 slots, all in the custom afternoon window.
    assert len(slots) == 4
    local_starts = [s.starts_at.astimezone(ZoneInfo("Europe/Warsaw")) for s in slots]
    assert [d.hour for d in local_starts] == [12, 12, 13, 13]
    assert [d.minute for d in local_starts] == [0, 30, 0, 30]
