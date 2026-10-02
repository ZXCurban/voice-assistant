"""Tool executor against real services (SQLite seed, no LLM needed)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import _update_context
from app.ai.tools import TOOL_NAMES, TOOLS, execute_tool
from app.assistant.schemas import AssistantContext
from tests.integration.helpers import future_monday, make_clinic


def test_update_context_folds_tool_results() -> None:
    context = AssistantContext()
    _update_context(
        "find_nearest_slots",
        {
            "status": "success",
            "code": "OK",
            "details": {
                "clinic_id": 1,
                "slots": [{"doctor": {"id": 2}, "specialty": {"id": 3}}],
            },
        },
        context,
    )
    assert context.clinic_id == 1
    assert context.selected_doctor_id == 2
    assert context.selected_specialty_id == 3
    # Failures must not pollute context.
    _update_context("find_slots", {"status": "not_found"}, context)
    assert context.patient_id is None


async def test_execute_uses_sticky_context(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Sticky Context")
    found = await execute_tool(
        db_session,
        "find_nearest_slots",
        {"clinic_id": refs["clinic_id"], "specialty_name": "Cardiology"},
    )
    assert found["status"] == "success"
    starts_at: str = found["details"]["slots"][0]["starts_at"]
    context = AssistantContext(clinic_id=refs["clinic_id"], patient_id=refs["patient_id"])
    booked = await execute_tool(
        db_session,
        "book_appointment",
        {
            "doctor_id": refs["doctor_id"],
            "starts_at": starts_at,
            "confirmed": True,
        },
        context=context,
    )
    assert booked["status"] == "success"
    assert booked["details"]["appointment"]["status"] == "booked"


async def test_execute_find_slots_on_seeded_clinic(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Clinic")
    result = await execute_tool(
        db_session,
        "find_slots",
        {"clinic_id": refs["clinic_id"], "date": future_monday().isoformat()},
    )
    # No specialty/doctor -> orchestrator asks for clarification, no crash.
    assert result["status"] == "need_clarification"

    result = await execute_tool(
        db_session,
        "find_slots",
        {
            "clinic_id": refs["clinic_id"],
            "specialty_name": "Cardiology",
            "date": future_monday().isoformat(),
        },
    )
    assert result["status"] == "success"
    assert len(result["details"]["slots"]) > 0
    slot = result["details"]["slots"][0]
    assert slot["doctor"]["full_name"] == "Jan Kowalski"
    assert slot["room"]["code"] == "A-101"


async def test_execute_unknown_tool_and_bad_args(db_session: AsyncSession) -> None:
    unknown = await execute_tool(db_session, "drop_database", {})
    assert unknown["code"] == "UNKNOWN_TOOL"

    bad = await execute_tool(db_session, "find_slots", {"clinic_id": "NaN"})
    assert bad["code"] == "INVALID_TOOL_ARGUMENTS"
    assert bad["details"]["errors"]


async def test_execute_booking_needs_confirmation(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Booking")
    found = await execute_tool(
        db_session,
        "find_slots",
        {
            "clinic_id": refs["clinic_id"],
            "specialty_name": "Cardiology",
            "date": future_monday().isoformat(),
        },
    )
    starts_at: str = found["details"]["slots"][0]["starts_at"]
    booking_args = {
        "clinic_id": refs["clinic_id"],
        "patient_id": refs["patient_id"],
        "doctor_id": refs["doctor_id"],
        "starts_at": starts_at,
    }
    preview = await execute_tool(db_session, "book_appointment", booking_args)
    assert preview["status"] == "confirmation_required"
    assert preview["requires_confirmation"] is True

    booked = await execute_tool(db_session, "book_appointment", {**booking_args, "confirmed": True})
    assert booked["status"] == "success"
    assert booked["details"]["appointment"]["status"] == "booked"


async def test_execute_find_patient(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Patient Lookup")
    found = await execute_tool(
        db_session,
        "find_patient",
        {"clinic_id": refs["clinic_id"], "phone": "+10000000001"},
    )
    assert found["status"] == "success"
    assert found["details"]["patient"]["full_name"] == "Test Patient"

    missing = await execute_tool(
        db_session,
        "find_patient",
        {"clinic_id": refs["clinic_id"], "phone": "+10000000000"},
    )
    assert (missing["status"], missing["code"]) == ("not_found", "PATIENT_NOT_FOUND")

    cross = await execute_tool(
        db_session,
        "find_patient",
        {"clinic_id": refs["clinic_id"] + 999, "phone": "+10000000001"},
    )
    assert cross["status"] in ("not_found", "invalid_input")


async def test_execute_booking_registers_patient(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Auto Register")
    found = await execute_tool(
        db_session,
        "find_nearest_slots",
        {"clinic_id": refs["clinic_id"], "specialty_name": "Cardiology"},
    )
    assert found["status"] == "success"
    starts_at: str = found["details"]["slots"][0]["starts_at"]
    booked = await execute_tool(
        db_session,
        "book_appointment",
        {
            "clinic_id": refs["clinic_id"],
            "full_name": "Voice Patient",
            "phone": "+12223334444",
            "doctor_id": refs["doctor_id"],
            "starts_at": starts_at,
            "confirmed": True,
        },
    )
    assert booked["status"] == "success"
    assert booked["details"]["appointment"]["patient"]["full_name"] == "Voice Patient"

    again = await execute_tool(
        db_session,
        "book_appointment",
        {
            "clinic_id": refs["clinic_id"],
            "full_name": "Voice Patient",
            "phone": "+12223334444",
            "doctor_id": refs["doctor_id"],
            "starts_at": starts_at,
            "confirmed": True,
        },
    )
    assert (again["status"], again["code"]) == ("conflict", "SLOT_ALREADY_BOOKED")


async def test_execute_find_clinics_ranked_by_city(db_session: AsyncSession) -> None:
    from app.assistant.orchestrator import AssistantOrchestrator
    from app.assistant.schemas import AssistantRequest
    from app.schemas.clinic import ClinicCreate
    from app.services import clinics as clinics_service

    await clinics_service.create_clinic(
        db_session,
        ClinicCreate(
            name="Geo Warsaw",
            timezone="Europe/Warsaw",
            city="Warszawa",
            address="Marszalkowska 1, Warszawa",
            latitude=52.2297,
            longitude=21.0122,
        ),
    )
    await clinics_service.create_clinic(
        db_session,
        ClinicCreate(
            name="Geo Lisbon",
            timezone="Europe/Lisbon",
            city="Lisboa",
            address="Av. Atlantica 10, Lisboa",
            latitude=38.7223,
            longitude=-9.1393,
        ),
    )
    orch = AssistantOrchestrator()
    ranked = await orch.handle(db_session, AssistantRequest(intent="find_clinics", city="Варшава"))
    assert ranked.status == "success"
    names = [c["name"] for c in ranked.details["clinics"]]
    assert names.index("Geo Warsaw") < names.index("Geo Lisbon")
    assert ranked.details["matched_city"] == "warszawa"

    plain = await orch.handle(db_session, AssistantRequest(intent="find_clinics"))
    assert plain.status == "success"
    assert "matched_city" not in plain.details


async def test_tool_registry_covers_orchestrator_tools() -> None:
    assert {
        "find_clinics",
        "find_specialties",
        "find_doctors",
        "find_slots",
        "find_nearest_slots",
        "find_patient",
        "create_patient",
        "book_appointment",
        "get_appointments",
        "reschedule_appointment",
        "cancel_appointment",
    } == TOOL_NAMES
    assert len(TOOLS) == len(TOOL_NAMES)


async def test_execute_find_nearest_slots(db_session: AsyncSession) -> None:
    refs = await make_clinic(db_session, name="Tool Nearest")
    result = await execute_tool(
        db_session,
        "find_nearest_slots",
        {"clinic_id": refs["clinic_id"], "specialty_name": "Cardiology"},
    )
    assert result["status"] == "success"
    assert result["details"]["date"]
    assert len(result["details"]["slots"]) > 0

    empty = await execute_tool(
        db_session,
        "find_nearest_slots",
        {
            "clinic_id": refs["clinic_id"],
            "specialty_name": "Cardiology",
            "date": "2026-10-03",
            "days_ahead": 1,
        },
    )
    assert empty["status"] == "not_found"
    assert empty["code"] == "NO_SLOTS_AVAILABLE"
    assert empty["details"]["checked_dates"] == ["2026-10-03"]
