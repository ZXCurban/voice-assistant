"""Appointment lifecycle: book / view / reschedule / cancel / complete."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.appointment import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    Appointment,
)
from app.repositories import appointments as appointments_repo
from app.schemas.appointment import AppointmentCreate
from app.services import availability as availability_service
from app.services import doctors as doctors_service
from app.services import patients as patients_service
from app.services.catalog import get_room
from app.services.common import coerce_utc, ensure_aware_utc, require_clinic


async def _slot_lookup(
    session: AsyncSession,
    clinic_id: int,
    doctor_id: int,
    starts_at: datetime,
    clinic_timezone: str,
) -> datetime:
    """Validate instant against the generated grid; return computed ends_at."""
    tz = ZoneInfo(clinic_timezone)
    target = starts_at.astimezone(tz).date()
    # NOTE: validated against real "now" — passing starts_at as now_utc
    # would filter the wanted slot itself out as "past".
    slots = await availability_service.get_doctor_slots(session, clinic_id, doctor_id, target)
    wanted = ensure_aware_utc(starts_at)
    for slot in slots:
        if ensure_aware_utc(slot.starts_at) == wanted:
            return ensure_aware_utc(slot.ends_at)
    raise ConflictError("slot unavailable")


async def book_appointment(session: AsyncSession, data: AppointmentCreate) -> Appointment:
    clinic = await require_clinic(session, data.clinic_id)
    if not clinic.active:
        raise ConflictError("clinic is inactive")
    doctor = await doctors_service.get_doctor(session, data.clinic_id, data.doctor_id)
    if not doctor.active:
        raise ConflictError("doctor is inactive")
    await patients_service.get_patient(session, data.clinic_id, data.patient_id)
    if data.room_id is not None:
        room = await get_room(session, data.clinic_id, data.room_id)
        if not room.active:
            raise ConflictError("room is inactive")

    starts_at = ensure_aware_utc(data.starts_at)
    if starts_at <= datetime.now(UTC):
        raise ConflictError("cannot book in the past")
    ends_at = await _slot_lookup(
        session, data.clinic_id, data.doctor_id, starts_at, clinic.timezone
    )

    reason = data.reason.strip() if data.reason else None
    appointment = Appointment(
        clinic_id=data.clinic_id,
        doctor_id=data.doctor_id,
        patient_id=data.patient_id,
        room_id=data.room_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status=STATUS_BOOKED,
        reason=reason or None,
    )
    session.add(appointment)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Concurrent request won the same (doctor, instant): the partial
        # unique index is the arbiter, not the pre-check above.
        await session.rollback()
        raise ConflictError("slot unavailable") from exc
    await session.commit()
    # Re-fetch with nested relations loaded (refresh() would expire them
    # and trigger lazy loads, which are illegal in async code).
    loaded = await appointments_repo.get_appointment(session, data.clinic_id, appointment.id)
    assert loaded is not None
    return loaded


async def get_appointment(
    session: AsyncSession, clinic_id: int, appointment_id: int
) -> Appointment:
    appointment = await appointments_repo.get_appointment(session, clinic_id, appointment_id)
    if appointment is None:
        raise NotFoundError("appointment not found")
    return appointment


async def list_appointments(
    session: AsyncSession,
    clinic_id: int,
    *,
    patient_id: int | None = None,
    doctor_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Appointment]:
    await require_clinic(session, clinic_id)
    if patient_id is None and doctor_id is None:
        raise ValueError("patient_id or doctor_id filter is required")
    if patient_id is not None:
        await patients_service.get_patient(session, clinic_id, patient_id)
    if doctor_id is not None:
        await doctors_service.get_doctor(session, clinic_id, doctor_id)
    return await appointments_repo.list_appointments(
        session,
        clinic_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        date_from=coerce_utc(date_from),
        date_to=coerce_utc(date_to),
        status=status,
        limit=limit,
        offset=offset,
    )


async def reschedule_appointment(
    session: AsyncSession, clinic_id: int, appointment_id: int, new_starts_at: datetime
) -> Appointment:
    clinic = await require_clinic(session, clinic_id)
    old = await get_appointment(session, clinic_id, appointment_id)
    if old.status != STATUS_BOOKED:
        raise ConflictError(f"appointment already {old.status}")
    wanted = ensure_aware_utc(new_starts_at)
    if wanted == ensure_aware_utc(old.starts_at):
        raise ConflictError("new time equals the current booking")
    if wanted <= datetime.now(UTC):
        raise ConflictError("cannot reschedule into the past")
    ends_at = await _slot_lookup(session, clinic_id, old.doctor_id, wanted, clinic.timezone)
    replacement = Appointment(
        clinic_id=clinic_id,
        doctor_id=old.doctor_id,
        patient_id=old.patient_id,
        room_id=old.room_id,
        starts_at=wanted,
        ends_at=ends_at,
        status=STATUS_BOOKED,
        reason=old.reason,
    )
    session.add(replacement)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("slot unavailable") from exc
    old.status = STATUS_CANCELLED
    await session.flush()
    await session.commit()
    loaded = await appointments_repo.get_appointment(session, clinic_id, replacement.id)
    assert loaded is not None
    return loaded


async def _transition(
    session: AsyncSession, clinic_id: int, appointment_id: int, to: str
) -> Appointment:
    await require_clinic(session, clinic_id)
    appointment = await get_appointment(session, clinic_id, appointment_id)
    if appointment.status != STATUS_BOOKED:
        raise ConflictError(f"appointment already {appointment.status}")
    appointment.status = to
    await session.flush()
    await session.commit()
    loaded = await appointments_repo.get_appointment(session, clinic_id, appointment.id)
    assert loaded is not None
    return loaded


async def cancel_appointment(
    session: AsyncSession, clinic_id: int, appointment_id: int
) -> Appointment:
    return await _transition(session, clinic_id, appointment_id, STATUS_CANCELLED)


async def complete_appointment(
    session: AsyncSession, clinic_id: int, appointment_id: int
) -> Appointment:
    return await _transition(session, clinic_id, appointment_id, STATUS_COMPLETED)
