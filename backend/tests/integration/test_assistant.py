"""AssistantOrchestrator flows over existing services (SQLite)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantRequest
from app.schemas.catalog import SpecialtyCreate
from app.schemas.doctor import DoctorCreate
from app.schemas.schedule import ScheduleExceptionCreate
from app.services import catalog as catalog_service
from app.services import doctors as doctors_service
from app.services import exceptions as exceptions_service
from tests.integration.helpers import future_monday, make_clinic

orchestrator = AssistantOrchestrator()


async def _refs(db_session: AsyncSession, name: str = "Assist Clinic") -> dict[str, int]:
    refs = await make_clinic(db_session, name=name)
    return dict(refs)


async def test_find_slots_by_specialty_name(db_session: AsyncSession) -> None:
    refs = await _refs(db_session)
    res = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            specialty_name="Cardiology",
            date=future_monday(),
        ),
    )
    assert res.status == "success" and res.code == "OK"
    assert len(res.details["slots"]) >= 2
    assert res.details["slots"][0]["specialty"]["name"] == "cardiology"


async def test_find_slots_missing_info(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Missing")
    no_clinic = await orchestrator.handle(db_session, AssistantRequest(intent="find_slots"))
    assert (no_clinic.status, no_clinic.code) == ("need_clarification", "CLINIC_REQUIRED")
    no_date = await orchestrator.handle(
        db_session,
        AssistantRequest(intent="find_slots", clinic_id=refs["clinic_id"]),
    )
    assert (no_date.status, no_date.code) == ("need_clarification", "DATE_REQUIRED")
    no_target = await orchestrator.handle(
        db_session,
        AssistantRequest(intent="find_slots", clinic_id=refs["clinic_id"], date=future_monday()),
    )
    assert (no_target.status, no_target.code) == (
        "need_clarification",
        "SPECIALTY_OR_DOCTOR_REQUIRED",
    )


async def test_find_slots_unknown_entities(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Unknown")
    unknown_spec = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            specialty_name="Astrology",
            date=future_monday(),
        ),
    )
    assert (unknown_spec.status, unknown_spec.code) == (
        "not_found",
        "SPECIALTY_NOT_FOUND",
    )
    unknown_doc = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_name="Nobody Here",
            date=future_monday(),
        ),
    )
    assert (unknown_doc.status, unknown_doc.code) == ("not_found", "DOCTOR_NOT_FOUND")


async def test_find_slots_ambiguous_doctor(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Ambiguous")
    second_spec = await catalog_service.create_specialty(
        db_session,
        SpecialtyCreate(clinic_id=refs["clinic_id"], name="Neurology"),
    )
    await doctors_service.create_doctor(
        db_session,
        DoctorCreate(
            clinic_id=refs["clinic_id"],
            full_name="Андрей Волков",
            specialty_id=second_spec.id,
        ),
    )
    res = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_name="андрей волков",
            date=future_monday(),
        ),
    )
    assert (res.status, res.code) == ("need_clarification", "AMBIGUOUS_DOCTOR")
    assert len(res.details["candidates"]) == 2


async def test_find_slots_none_available(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Empty")
    monday = future_monday()
    await exceptions_service.create_exception(
        db_session,
        ScheduleExceptionCreate(
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
            kind="day_off",
        ),
    )
    res = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
        ),
    )
    assert (res.status, res.code) == ("not_found", "NO_SLOTS_AVAILABLE")


async def test_book_confirmation_then_execute(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Booking")
    monday = future_monday()
    found = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
        ),
    )
    instant = found.details["slots"][0]["starts_at"]
    base = {
        "intent": "book_appointment",
        "clinic_id": refs["clinic_id"],
        "patient_id": refs["patient_id"],
        "doctor_id": refs["doctor_id"],
        "starts_at": instant,
    }
    preview = await orchestrator.handle(
        db_session,
        AssistantRequest(**base),  # type: ignore[arg-type]
    )
    assert preview.status == "confirmation_required"
    assert preview.requires_confirmation is True
    assert preview.code == "CONFIRM_BOOKING"
    assert preview.details["slot"]["starts_at"] == instant

    booked = await orchestrator.handle(
        db_session,
        AssistantRequest(**base, confirmed=True),  # type: ignore[arg-type]
    )
    assert (booked.status, booked.code) == ("success", "APPOINTMENT_BOOKED")
    assert booked.details["appointment"]["doctor"]["full_name"] == "Андрей Волков"

    double = await orchestrator.handle(
        db_session,
        AssistantRequest(**base, confirmed=True),  # type: ignore[arg-type]
    )
    assert (double.status, double.code) == ("conflict", "SLOT_ALREADY_BOOKED")


async def test_book_rejects_bad_slot(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Bad Slot")
    off_grid = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="book_appointment",
            clinic_id=refs["clinic_id"],
            patient_id=refs["patient_id"],
            doctor_id=refs["doctor_id"],
            starts_at=datetime.now(UTC) + timedelta(days=8, minutes=7),
            confirmed=True,
        ),
    )
    assert off_grid.status == "conflict"
    past = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="book_appointment",
            clinic_id=refs["clinic_id"],
            patient_id=refs["patient_id"],
            doctor_id=refs["doctor_id"],
            starts_at=datetime(2020, 1, 6, 8, 0, tzinfo=UTC),
            confirmed=True,
        ),
    )
    assert past.status == "conflict"


async def test_reschedule_and_cancel_flows(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Lifecycle")
    monday = future_monday()
    found = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
        ),
    )
    slots = found.details["slots"]
    booked = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="book_appointment",
            clinic_id=refs["clinic_id"],
            patient_id=refs["patient_id"],
            doctor_id=refs["doctor_id"],
            starts_at=slots[0]["starts_at"],
            confirmed=True,
        ),
    )
    appointment_id = booked.details["appointment"]["id"]

    preview = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="reschedule_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=appointment_id,
            new_starts_at=slots[1]["starts_at"],
        ),
    )
    assert (preview.status, preview.code) == (
        "confirmation_required",
        "CONFIRM_RESCHEDULE",
    )
    moved = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="reschedule_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=appointment_id,
            new_starts_at=slots[1]["starts_at"],
            confirmed=True,
        ),
    )
    assert (moved.status, moved.code) == ("success", "APPOINTMENT_RESCHEDULED")
    new_id = moved.details["appointment"]["id"]

    cancel_preview = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="cancel_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=new_id,
        ),
    )
    assert (cancel_preview.status, cancel_preview.code) == (
        "confirmation_required",
        "CONFIRM_CANCEL",
    )
    cancelled = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="cancel_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=new_id,
            confirmed=True,
        ),
    )
    assert (cancelled.status, cancelled.code) == ("success", "APPOINTMENT_CANCELLED")

    again = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="cancel_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=new_id,
            confirmed=True,
        ),
    )
    assert again.status == "conflict"
    assert again.code == "APPOINTMENT_ALREADY_CANCELLED"

    unknown = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="cancel_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=999999,
            confirmed=True,
        ),
    )
    assert (unknown.status, unknown.code) == ("not_found", "APPOINTMENT_NOT_FOUND")


async def test_get_appointments_flow(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Listing")
    listed = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="get_appointments",
            clinic_id=refs["clinic_id"],
            patient_id=refs["patient_id"],
        ),
    )
    assert listed.status == "success"
    assert listed.details["appointments"] == []
    unscoped = await orchestrator.handle(
        db_session,
        AssistantRequest(intent="get_appointments", clinic_id=refs["clinic_id"]),
    )
    assert unscoped.status == "invalid_input"


async def test_assistant_cross_tenant_isolation(db_session: AsyncSession) -> None:
    clinic_a = await _refs(db_session, "Assist Tenant A")
    clinic_b = await _refs(db_session, "Assist Tenant B")

    cross_doctor = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=clinic_a["clinic_id"],
            doctor_id=clinic_b["doctor_id"],
            date=future_monday(),
        ),
    )
    assert cross_doctor.status == "not_found"

    cross_spec = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=clinic_a["clinic_id"],
            specialty_id=clinic_b["specialty_id"],
            date=future_monday(),
        ),
    )
    assert cross_spec.status == "not_found"

    monday = future_monday()
    found = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=clinic_a["clinic_id"],
            doctor_id=clinic_a["doctor_id"],
            date=monday,
        ),
    )
    booked = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="book_appointment",
            clinic_id=clinic_a["clinic_id"],
            patient_id=clinic_a["patient_id"],
            doctor_id=clinic_a["doctor_id"],
            starts_at=found.details["slots"][0]["starts_at"],
            confirmed=True,
        ),
    )
    appointment_id = booked.details["appointment"]["id"]
    cross_appointment = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="get_appointment",
            clinic_id=clinic_b["clinic_id"],
            appointment_id=appointment_id,
        ),
    )
    assert cross_appointment.status == "not_found"


async def test_complete_flow_confirmation(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Complete")
    monday = future_monday()
    found = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_slots",
            clinic_id=refs["clinic_id"],
            doctor_id=refs["doctor_id"],
            date=monday,
        ),
    )
    booked = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="book_appointment",
            clinic_id=refs["clinic_id"],
            patient_id=refs["patient_id"],
            doctor_id=refs["doctor_id"],
            starts_at=found.details["slots"][0]["starts_at"],
            confirmed=True,
        ),
    )
    appointment_id = booked.details["appointment"]["id"]
    preview = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="complete_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=appointment_id,
        ),
    )
    assert (preview.status, preview.code) == (
        "confirmation_required",
        "CONFIRM_COMPLETE",
    )
    done = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="complete_appointment",
            clinic_id=refs["clinic_id"],
            appointment_id=appointment_id,
            confirmed=True,
        ),
    )
    assert (done.status, done.code) == ("success", "APPOINTMENT_COMPLETED")
    assert done.details["appointment"]["status"] == "completed"


async def test_find_helpers_and_create_patient(db_session: AsyncSession) -> None:
    refs = await _refs(db_session, "Assist Helpers")
    clinics = await orchestrator.handle(db_session, AssistantRequest(intent="find_clinics"))
    assert clinics.status == "success"
    assert any(c["id"] == refs["clinic_id"] for c in clinics.details["clinics"])

    specs = await orchestrator.handle(
        db_session,
        AssistantRequest(intent="find_specialties", clinic_id=refs["clinic_id"]),
    )
    assert specs.status == "success"

    doctors = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="find_doctors",
            clinic_id=refs["clinic_id"],
            specialty_name="cardiology",
        ),
    )
    assert doctors.status == "success"
    assert doctors.details["doctors"][0]["specialty"]["name"] == "cardiology"

    created = await orchestrator.handle(
        db_session,
        AssistantRequest(
            intent="create_patient",
            clinic_id=refs["clinic_id"],
            full_name="Voice Patient",
            phone="+70000000001",
        ),
    )
    assert (created.status, created.code) == ("success", "PATIENT_CREATED")

    incomplete = await orchestrator.handle(
        db_session,
        AssistantRequest(intent="create_patient", clinic_id=refs["clinic_id"]),
    )
    assert incomplete.status == "invalid_input"
