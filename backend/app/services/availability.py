"""Slot generation: clinic hours ∩ doctor hours ∩ exceptions − booked.

Pure interval math (no DB) lives in LocalInterval helpers so it is unit
testable; async functions below orchestrate repository loads.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.clinic import Clinic
from app.models.doctor import Doctor
from app.models.doctor_schedule import DoctorSchedule
from app.models.schedule_exception import (
    EXCEPTION_CUSTOM_HOURS,
    EXCEPTION_DAY_OFF,
    ScheduleException,
)
from app.repositories import appointments as appointments_repo
from app.repositories import catalog as catalog_repo
from app.repositories import exceptions as exceptions_repo
from app.repositories import schedules as schedules_repo
from app.schemas.slot import (
    SlotDoctorRef,
    SlotOut,
    SlotRoomRef,
    SlotSpecialtyRef,
)
from app.services import catalog as catalog_service
from app.services import doctors as doctors_service
from app.services.common import ensure_aware_utc, require_clinic

MAX_HORIZON_DAYS = 90


@dataclass(frozen=True)
class LocalInterval:
    """Half-open [start, end) wall-clock interval in clinic-local time."""

    start: time
    end: time


def intersect(a: list[LocalInterval], b: list[LocalInterval]) -> list[LocalInterval]:
    """Pairwise intersection of two interval lists (sorted output)."""
    out: list[LocalInterval] = []
    for x in a:
        for y in b:
            start = max(x.start, y.start)
            end = min(x.end, y.end)
            if start < end:
                out.append(LocalInterval(start, end))
    return sorted(out, key=lambda i: (i.start, i.end))


def expand_slots(intervals: list[LocalInterval], slot_minutes: int) -> list[LocalInterval]:
    """Split intervals into aligned fixed slots; drops a short tail."""
    step = timedelta(minutes=slot_minutes)
    slots: list[LocalInterval] = []
    for interval in intervals:
        cursor = datetime.combine(date.min, interval.start)
        end = datetime.combine(date.min, interval.end)
        while cursor + step <= end:
            slots.append(LocalInterval(cursor.time(), (cursor + step).time()))
            cursor += step
    return slots


def compute_day_slots(
    *,
    clinic_tz: ZoneInfo,
    target: date,
    clinic_intervals: list[LocalInterval],
    doctor_intervals: list[LocalInterval],
    slot_minutes: int,
    booked_starts_utc: set[datetime],
    now_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    """Pure slot computation for one doctor/day. Returns aware UTC pairs."""
    effective = intersect(clinic_intervals, doctor_intervals)
    result: list[tuple[datetime, datetime]] = []
    for slot in expand_slots(effective, slot_minutes):
        starts_at = datetime.combine(target, slot.start, tzinfo=clinic_tz).astimezone(UTC)
        ends_at = starts_at + timedelta(minutes=slot_minutes)
        if starts_at <= now_utc:
            continue
        if starts_at in booked_starts_utc:
            continue
        result.append((starts_at, ends_at))
    return result


def filter_slots_after_local_time(
    slots: list[SlotOut],
    *,
    clinic_timezone: str,
    time_after: time | None = None,
    time_at: time | None = None,
) -> list[SlotOut]:
    """Filter generated UTC slots using clinic-local spoken time constraints."""
    tz = ZoneInfo(clinic_timezone)
    matching: list[SlotOut] = []
    for slot in slots:
        local_time = slot.starts_at.astimezone(tz).time().replace(tzinfo=None)
        if time_at is not None and local_time != time_at:
            continue
        if time_after is not None and local_time <= time_after:
            continue
        matching.append(slot)
    return matching


def _validate_target(target: date, now_utc: datetime, tz: ZoneInfo) -> None:
    local_today = now_utc.astimezone(tz).date()
    if (target - local_today).days > MAX_HORIZON_DAYS:
        raise ValueError(f"date is beyond the {MAX_HORIZON_DAYS}-day horizon")


def _resolve_clinic_intervals(
    weekly: list[LocalInterval], day_exceptions: list[ScheduleException]
) -> list[LocalInterval] | None:
    """Apply clinic-level exceptions. None means the clinic is closed."""
    intervals = weekly
    for exc in day_exceptions:
        if exc.doctor_id is not None:
            continue
        if exc.kind == EXCEPTION_DAY_OFF:
            return None
        if exc.kind == EXCEPTION_CUSTOM_HOURS:
            assert exc.start_local is not None and exc.end_local is not None
            intervals = [LocalInterval(exc.start_local, exc.end_local)]
    return intervals


def _resolve_doctor_intervals(
    weekly: list[DoctorSchedule],
    day_exceptions: list[ScheduleException],
    doctor_id: int,
) -> list[LocalInterval] | None:
    """Apply doctor-level exceptions. None means the doctor is off."""
    for exc in day_exceptions:
        if exc.doctor_id != doctor_id:
            continue
        if exc.kind == EXCEPTION_DAY_OFF:
            return None
        if exc.kind == EXCEPTION_CUSTOM_HOURS:
            assert exc.start_local is not None and exc.end_local is not None
            return [LocalInterval(exc.start_local, exc.end_local)]
    return [LocalInterval(r.start_local, r.end_local) for r in weekly]


async def _room_ref(
    session: AsyncSession, doctor: Doctor, room_id: int | None
) -> SlotRoomRef | None:
    if room_id is None:
        return None
    room = await catalog_repo.get_room(session, doctor.clinic_id, room_id)
    if room is None or not room.active:
        return None
    return SlotRoomRef(id=room.id, code=room.code, label=room.label)


async def get_doctor_slots(
    session: AsyncSession,
    clinic_id: int,
    doctor_id: int,
    target: date,
    *,
    now_utc: datetime | None = None,
) -> list[SlotOut]:
    """Available slots for one doctor on one clinic-local date."""
    now = ensure_aware_utc(now_utc) if now_utc is not None else datetime.now(UTC)
    clinic: Clinic = await require_clinic(session, clinic_id)
    try:
        tz = ZoneInfo(clinic.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid timezone: {clinic.timezone!r}") from exc
    _validate_target(target, now, tz)
    doctor = await doctors_service.get_doctor(session, clinic_id, doctor_id)
    if not clinic.active:
        raise ConflictError("clinic is inactive")
    if not doctor.active:
        raise ConflictError("doctor is inactive")
    weekday = target.weekday()
    clinic_rows = await schedules_repo.list_clinic_schedules(session, clinic.id)
    clinic_weekly = [
        LocalInterval(r.start_local, r.end_local) for r in clinic_rows if r.weekday == weekday
    ]
    day_exceptions = await exceptions_repo.get_exceptions_for_date(session, clinic.id, target)
    resolved_clinic = _resolve_clinic_intervals(clinic_weekly, day_exceptions)
    if not resolved_clinic:
        return []
    doctor_rows = await schedules_repo.list_doctor_schedules(session, clinic_id, doctor_id)
    active_rows = [r for r in doctor_rows if r.weekday == weekday]
    if not active_rows:
        return []
    resolved_doctor = _resolve_doctor_intervals(active_rows, day_exceptions, doctor_id)
    if not resolved_doctor:
        return []
    slot_minutes = active_rows[0].slot_minutes

    day_start = datetime.combine(target, time.min, tzinfo=tz).astimezone(UTC)
    day_end = day_start + timedelta(days=1)
    booked = {
        ensure_aware_utc(s)
        for s in await appointments_repo.list_booked_starts(
            session, clinic.id, doctor_id, day_start, day_end
        )
    }
    pairs = compute_day_slots(
        clinic_tz=tz,
        target=target,
        clinic_intervals=resolved_clinic,
        doctor_intervals=resolved_doctor,
        slot_minutes=slot_minutes,
        booked_starts_utc=booked,
        now_utc=now,
    )
    room_id = next((r.room_id for r in active_rows if r.room_id is not None), None)
    room_ref = await _room_ref(session, doctor, room_id)
    return [
        SlotOut(
            doctor=SlotDoctorRef(id=doctor.id, full_name=doctor.full_name),
            specialty=SlotSpecialtyRef(id=doctor.specialty.id, name=doctor.specialty.name),
            starts_at=start,
            ends_at=end,
            room=room_ref,
        )
        for start, end in pairs
    ]


async def search_slots(
    session: AsyncSession,
    clinic_id: int,
    target: date,
    *,
    specialty_id: int | None = None,
    doctor_id: int | None = None,
    now_utc: datetime | None = None,
    limit: int = 50,
) -> list[SlotOut]:
    """Generalized search for the voice assistant: one specialty or doctor."""
    if (specialty_id is None) == (doctor_id is None):
        raise ValueError("exactly one of specialty_id, doctor_id is required")
    clinic = await require_clinic(session, clinic_id)
    if not clinic.active:
        raise ConflictError("clinic is inactive")
    doctors: list[Doctor]
    if specialty_id is not None:
        await catalog_service.get_specialty(session, clinic_id, specialty_id)
        doctors = await doctors_service.list_doctors(
            session, clinic_id, specialty_id=specialty_id, active_only=True
        )
        if not doctors:
            raise NotFoundError("no active doctors found")
    else:
        assert doctor_id is not None
        doctor = await doctors_service.get_doctor(session, clinic_id, doctor_id)
        if not doctor.active:
            raise ConflictError("doctor is inactive")
        doctors = [doctor]
    out: list[SlotOut] = []
    for doctor in doctors:
        out.extend(await get_doctor_slots(session, clinic_id, doctor.id, target, now_utc=now_utc))
        if len(out) >= limit:
            break
    out.sort(key=lambda s: (s.starts_at, s.doctor.id))
    return out[:limit]
