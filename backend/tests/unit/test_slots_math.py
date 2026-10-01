"""Unit tests for pure slot math (no DB): intersection, grid, TZ, filters."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.services.availability import (
    LocalInterval,
    compute_day_slots,
    expand_slots,
    intersect,
)


def test_intersect_basic() -> None:
    a = [LocalInterval(time(8, 0), time(20, 0))]
    b = [LocalInterval(time(9, 0), time(13, 0))]
    assert intersect(a, b) == [LocalInterval(time(9, 0), time(13, 0))]


def test_intersect_split_shift() -> None:
    clinic = [LocalInterval(time(8, 0), time(12, 0)), LocalInterval(time(14, 0), time(20, 0))]
    doctor = [LocalInterval(time(10, 0), time(16, 0))]
    assert intersect(clinic, doctor) == [
        LocalInterval(time(10, 0), time(12, 0)),
        LocalInterval(time(14, 0), time(16, 0)),
    ]


def test_intersect_empty() -> None:
    assert intersect([], [LocalInterval(time(9, 0), time(10, 0))]) == []


def test_expand_slots_drops_short_tail() -> None:
    slots = expand_slots([LocalInterval(time(9, 0), time(10, 10))], 30)
    assert slots == [
        LocalInterval(time(9, 0), time(9, 30)),
        LocalInterval(time(9, 30), time(10, 0)),
    ]


def test_compute_day_slots_warsaw_winter() -> None:
    """09:00 local in January = 08:00 UTC (CET, UTC+1)."""
    tz = ZoneInfo("Europe/Warsaw")
    target = date(2026, 1, 12)  # a Monday
    pairs = compute_day_slots(
        clinic_tz=tz,
        target=target,
        clinic_intervals=[LocalInterval(time(8, 0), time(20, 0))],
        doctor_intervals=[LocalInterval(time(9, 0), time(10, 0))],
        slot_minutes=30,
        booked_starts_utc=set(),
        now_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert [s for s, _ in pairs] == [
        datetime(2026, 1, 12, 8, 0, tzinfo=UTC),
        datetime(2026, 1, 12, 8, 30, tzinfo=UTC),
    ]
    assert pairs[0][1] == datetime(2026, 1, 12, 8, 30, tzinfo=UTC)


def test_compute_day_slots_summer_offset() -> None:
    """09:00 local in July = 07:00 UTC (CEST, UTC+2) — DST handled."""
    tz = ZoneInfo("Europe/Warsaw")
    target = date(2026, 7, 13)
    pairs = compute_day_slots(
        clinic_tz=tz,
        target=target,
        clinic_intervals=[LocalInterval(time(8, 0), time(20, 0))],
        doctor_intervals=[LocalInterval(time(9, 0), time(9, 30))],
        slot_minutes=30,
        booked_starts_utc=set(),
        now_utc=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert pairs[0][0] == datetime(2026, 7, 13, 7, 0, tzinfo=UTC)


def test_compute_day_slots_skips_booked_and_past() -> None:
    tz = ZoneInfo("Europe/Warsaw")
    target = date(2026, 1, 12)
    booked = {datetime(2026, 1, 12, 8, 30, tzinfo=UTC)}
    pairs = compute_day_slots(
        clinic_tz=tz,
        target=target,
        clinic_intervals=[LocalInterval(time(8, 0), time(20, 0))],
        doctor_intervals=[LocalInterval(time(9, 0), time(10, 0))],
        slot_minutes=30,
        booked_starts_utc=booked,
        # "now" is 08:15 UTC — the 08:00 slot is in the past, 08:30 booked.
        now_utc=datetime(2026, 1, 12, 8, 15, tzinfo=UTC),
    )
    assert pairs == []
