"""Deterministic multi-turn state machine tests without external infrastructure."""

from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.dialogue import DialogueState
from app.assistant.dialogue_manager import DialogueManager
from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantRequest, AssistantResult


class _BookingBackend(AssistantOrchestrator):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def handle(self, session: AsyncSession, request: AssistantRequest) -> AssistantResult:
        _ = session
        self.calls.append(request.model_dump(mode="json"))
        if request.intent == "find_specialties":
            return AssistantResult(
                status="success",
                code="OK",
                message="found",
                details={"specialties": [{"id": 17, "name": "cardiology"}]},
            )
        if request.intent == "find_clinics":
            return AssistantResult(
                status="success",
                code="OK",
                message="clinics",
                details={
                    "clinics": [
                        {"id": 3, "city": "Warszawa"},
                        {"id": 4, "city": "Lisboa"},
                    ]
                },
            )
        if request.intent in {"find_slots", "find_nearest_slots"}:
            result_date = request.date or date.today() + timedelta(days=1)
            slots = [
                {
                    "starts_at": f"{result_date.isoformat()}T{hour:02}:00:00+00:00",
                    "doctor": {"id": 23, "full_name": "Ivan Petrov"},
                }
                for hour in (14, 15, 16)
            ]
            return AssistantResult(
                status="success",
                code="OK",
                message="slots",
                details={
                    "slots": slots,
                    "date": result_date.isoformat(),
                    "timezone": "UTC",
                },
            )
        if request.intent == "find_doctors":
            return AssistantResult(
                status="success",
                code="OK",
                message="doctors",
                details={"doctors": [{"id": 23, "full_name": "Ivan Petrov"}]},
            )
        if request.intent == "find_patient":
            return AssistantResult(
                status="not_found",
                code="PATIENT_NOT_FOUND",
                message="patient not found",
                details={},
            )
        if request.intent == "book_appointment" and not request.confirmed:
            return AssistantResult(
                status="confirmation_required",
                code="CONFIRM_BOOKING",
                message="confirm",
                details={},
            )
        if request.intent == "book_appointment" and request.confirmed:
            return AssistantResult(
                status="success",
                code="APPOINTMENT_BOOKED",
                message="booked",
                details={"appointment": {"id": 99}},
            )
        raise AssertionError(f"Unexpected backend action: {request.intent}")


async def test_booking_is_stateful_and_mutates_only_once_after_confirmation() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    state = DialogueState(conversation_id="acceptance", clinic_id=3, patient_id=41)
    session = cast(AsyncSession, None)

    first = await manager.handle(session, state, "Хочу записаться к кардиологу.")
    assert "дату" in first
    assert state.intent == "book_appointment"
    assert state.specialty_name == "cardiology"

    second = await manager.handle(session, state, "Завтра после двух.")
    assert "Доступное время" in second
    assert len(state.available_slots) == 3
    assert state.time_after is not None
    assert state.time_after.hour == 14

    third = await manager.handle(session, state, "Второй.")
    assert "Записать вас" in third
    assert state.selected_slot is not None
    assert state.selected_slot["starts_at"].endswith("15:00:00+00:00")
    assert state.phase == "CONFIRMING"

    fourth = await manager.handle(session, state, "Да.")
    assert "запись оформлена" in fourth.casefold()
    assert state.phase == "COMPLETED"
    assert sum(call["intent"] == "book_appointment" for call in backend.calls) == 2
    assert backend.calls[-1]["confirmed"] is True

    await manager.handle(session, state, "Да.")
    assert sum(call["intent"] == "book_appointment" for call in backend.calls) == 2


async def test_doctor_search_and_nearest_window_followups() -> None:
    manager = DialogueManager(_BookingBackend())
    session = cast(AsyncSession, None)

    doctors_state = DialogueState(conversation_id="doctor-search", clinic_id=3)
    found = await manager.handle(session, doctors_state, "Мне нужен кардиолог")
    assert "Ivan Petrov" in found

    booking_state = DialogueState(conversation_id="nearest", clinic_id=3)
    await manager.handle(session, booking_state, "Хочу записаться к кардиологу")
    nearest = await manager.handle(session, booking_state, "Какое ближайшее свободное окно?")
    assert "Доступное время" in nearest
    assert len(booking_state.available_slots) == 3
    assert booking_state.selected_slot is None
    assert booking_state.date is not None


async def test_booking_after_single_doctor_search_keeps_doctor_reference() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    state = DialogueState(conversation_id="book-doctor-reference", clinic_id=3)
    session = cast(AsyncSession, None)

    found = await manager.handle(session, state, "Мне нужен кардиолог")
    followup = await manager.handle(session, state, "Запиши меня к нему")
    slots = await manager.handle(session, state, "Завтра")

    assert "Ivan Petrov" in found
    assert "дату" in followup
    assert "Доступное время" in slots
    assert state.intent == "book_appointment"
    assert state.doctor_name == "Ivan Petrov"
    slot_search = next(call for call in backend.calls if call["intent"] == "find_slots")
    assert slot_search["doctor_id"] == 23
    assert slot_search["specialty_id"] is None


async def test_greeting_during_booking_repeats_the_pending_question() -> None:
    manager = DialogueManager(_BookingBackend())
    state = DialogueState(conversation_id="greeting-followup", clinic_id=3)
    session = cast(AsyncSession, None)
    await manager.handle(session, state, "Хочу записаться к кардиологу")

    reply = await manager.handle(session, state, "Привет")

    assert "Продолжим" in reply
    assert "дату" in reply


async def test_unknown_patient_phone_gets_a_specific_recoverable_response() -> None:
    manager = DialogueManager(_BookingBackend())
    state = DialogueState(conversation_id="unknown-patient", clinic_id=3)
    session = cast(AsyncSession, None)
    await manager.handle(
        session,
        state,
        "Хочу записаться к кардиологу завтра в 10, телефон 89634037542",
    )

    reply = await manager.handle(session, state, "Второй")

    assert "записываетесь впервые" in reply
    assert state.patient_mode is None
    assert state.intent == "book_appointment"


async def test_first_time_patient_onboarding_keeps_booking_context() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    state = DialogueState(conversation_id="onboarding", clinic_id=3)
    session = cast(AsyncSession, None)

    await manager.handle(session, state, "Запишите к кардиологу завтра")
    slots = await manager.handle(session, state, "Первый")
    assert "впервые" in slots
    assert state.selected_slot is not None

    name_prompt = await manager.handle(session, state, "Я впервые")
    assert "имя и фамилию" in name_prompt
    assert state.patient_mode == "new"
    assert state.awaiting_input == "full_name"

    phone_prompt = await manager.handle(session, state, "Курбан Далгатов")
    assert "номер телефона" in phone_prompt
    assert state.patient_full_name == "Курбан Далгатов"
    assert state.awaiting_input == "phone"

    confirmation = await manager.handle(session, state, "+79990001122")
    assert "Создать профиль" in confirmation
    assert state.phase == "CONFIRMING"
    assert sum(call["intent"] == "book_appointment" for call in backend.calls) == 1

    booked = await manager.handle(session, state, "Да")
    assert "запись оформлена" in booked.casefold()
    assert state.phase == "COMPLETED"
    assert sum(call["intent"] == "book_appointment" for call in backend.calls) == 2
    assert backend.calls[-1]["confirmed"] is True


async def test_repeated_find_prompt_explains_the_missing_city() -> None:
    manager = DialogueManager(_BookingBackend())
    state = DialogueState(conversation_id="find-prompt")
    session = cast(AsyncSession, None)
    await manager.handle(session, state, "Мне нужен кардиолог")

    reply = await manager.handle(session, state, "Найди")

    assert "ищу кардиолога" in reply
    assert "В каком городе" in reply


async def test_first_time_patient_is_only_attached_to_confirmable_booking() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    state = DialogueState(
        conversation_id="new-patient",
        phase="WAITING_CLARIFICATION",
        intent="book_appointment",
        clinic_id=3,
        specialty_name="cardiology",
        date=date.today() + timedelta(days=1),
        patient_phone="+79990001122",
        patient_mode="new",
        patient_lookup_failed=True,
        awaiting_input="full_name",
        selected_slot={
            "starts_at": f"{date.today() + timedelta(days=1)}T14:00:00+00:00",
            "doctor": {"id": 23, "full_name": "Ivan Petrov"},
        },
    )

    reply = await manager.handle(cast(AsyncSession, None), state, "Меня зовут Алексей Иванов")

    assert "Алексей Иванов" in reply
    preview = next(call for call in backend.calls if call["intent"] == "book_appointment")
    assert preview["confirmed"] is False
    assert preview["full_name"] == "Алексей Иванов"
    assert preview["phone"] == "+79990001122"


async def test_new_patient_can_provide_plain_name_after_phone_lookup_fails() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    state = DialogueState(
        conversation_id="plain-new-patient-name",
        phase="WAITING_CLARIFICATION",
        intent="book_appointment",
        clinic_id=3,
        specialty_name="cardiology",
        date=date.today() + timedelta(days=1),
        patient_phone="+79990001122",
        patient_mode="new",
        patient_lookup_failed=True,
        awaiting_input="full_name",
        selected_slot={
            "starts_at": f"{date.today() + timedelta(days=1)}T14:00:00+00:00",
            "doctor": {"id": 23, "full_name": "Ivan Petrov"},
        },
    )

    reply = await manager.handle(cast(AsyncSession, None), state, "Курбан Далгатов")

    assert "Курбан Далгатов" in reply
    preview = next(call for call in backend.calls if call["intent"] == "book_appointment")
    assert preview["full_name"] == "Курбан Далгатов"
    assert preview["confirmed"] is False


async def test_slot_can_be_selected_by_listed_local_time() -> None:
    backend = _BookingBackend()
    manager = DialogueManager(backend)
    result_date = date.today() + timedelta(days=1)
    state = DialogueState(
        conversation_id="slot-time-selection",
        phase="SELECTING",
        intent="book_appointment",
        clinic_id=3,
        clinic_timezone="UTC",
        specialty_name="cardiology",
        specialty_id=17,
        patient_id=41,
        date=result_date,
        available_slots=[
            {
                "starts_at": f"{result_date.isoformat()}T{hour:02}:00:00+00:00",
                "doctor": {"id": 23, "full_name": "Ivan Petrov"},
            }
            for hour in (9, 10, 11)
        ],
    )

    reply = await manager.handle(cast(AsyncSession, None), state, "10:00")

    assert "Записать вас" in reply
    assert state.selected_slot is not None
    assert state.selected_slot["starts_at"].endswith("T10:00:00+00:00")
