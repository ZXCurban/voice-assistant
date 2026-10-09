from datetime import date
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as chat_service
from app.ai.schemas import ChatResponse
from app.assistant.nlu import parse_utterance
from app.assistant.normalizer import normalize, normalize_date, normalize_time
from app.assistant.response import render_event, render_response
from app.assistant.schemas import AssistantResult


def test_intent_and_slot_extraction_russian() -> None:
    parsed = parse_utterance("Мне нужно записаться к кардиологу завтра после двух")
    assert parsed.intent == "book_appointment"
    assert parsed.confidence >= 0.8
    assert parsed.slots["specialty"].value == "cardiology"
    assert parsed.slots["date"].value == "tomorrow"
    assert parsed.slots["time_after"].value == "14:00"


def test_imperative_booking_form_is_recognized_without_fake_doctor() -> None:
    parsed = parse_utterance("Запишите к кардиологу завтра в 10")
    assert parsed.intent == "book_appointment"
    assert parsed.slots["specialty"].value == "cardiology"
    assert "doctor" not in parsed.slots


def test_common_search_and_nearest_window_phrases_are_understood() -> None:
    cases = {
        "мне нужен кардиолог": "find_doctors",
        "найди": "find_doctors",
        "найди мне врача кардиолога": "find_doctors",
        "какое ближайшее свободное окно?": "find_nearest_slots",
    }
    for utterance, intent in cases.items():
        assert parse_utterance(utterance).intent == intent
    assert parse_utterance("найди мне врача кардиолога").slots["specialty"].value == "cardiology"


def test_low_confidence_and_unrecognized_slots_are_not_guessed() -> None:
    parsed = parse_utterance("ну как-то там к доктору")
    assert parsed.intent == "unknown_request"
    assert parsed.confidence < 0.7
    assert "specialty" not in parsed.slots


def test_nlu_keeps_phone_digits_for_format_independent_lookup() -> None:
    parsed = parse_utterance("Мой номер +7 900 000 00 01")
    assert parsed.slots["phone"].value == "+79000000001"


def test_nlu_extracts_name_after_explicit_self_identification() -> None:
    parsed = parse_utterance("Меня зовут Алексей Иванов")
    assert parsed.slots["full_name"].value == "Алексей Иванов"


def test_nlu_handles_relative_and_spoken_calendar_dates() -> None:
    assert parse_utterance("послезавтра").slots["date"].value == "day_after_tomorrow"
    assert normalize_date("day_after_tomorrow", date(2026, 10, 6)) == date(2026, 10, 8)
    assert normalize_date(
        parse_utterance("7 октября").slots["date"].value, date(2026, 10, 6)
    ) == date(2026, 10, 7)
    assert normalize_date(
        parse_utterance("Октябрь 7").slots["date"].value, date(2026, 10, 6)
    ) == date(2026, 10, 7)


def test_nlu_recognizes_clock_time_as_slot_selection_and_health_concern() -> None:
    selection = parse_utterance("10:20")
    assert selection.intent == "select_option"
    assert selection.slots["selection_time"].value == "10:20"
    assert parse_utterance("у меня болит голова").intent == "health_concern"
    assert parse_utterance("у меня боит нога").intent == "health_concern"
    assert "doctor" not in parse_utterance("запиши меня к нему").slots


def test_nlu_extracts_explicit_name_with_transcription_typos() -> None:
    parsed = parse_utterance("Я записываюсь впервые. имя и фамилимя - Курбан Далгатов")
    assert parsed.slots["full_name"].value == "Курбан Далгатов"


def test_negation_and_user_correction_do_not_keep_the_old_entity() -> None:
    negated = parse_utterance("Не отменяйте мою запись")
    assert negated.intent == "unknown_request"
    corrected = parse_utterance("Не к кардиологу, а лучше к неврологу")
    assert corrected.slots["specialty"].value == "neurology"


def test_synonyms_and_date_time_normalization() -> None:
    parsed = parse_utterance("Запишите к окулисту 07.10.2026 в 14:30")
    normalized = normalize(parsed, clinic_id=17)
    assert normalized["specialty_name"] == "ophthalmology"
    assert normalize_date("tomorrow", date(2026, 10, 6)) == date(2026, 10, 7)
    assert normalize_time("14:30").isoformat() == "14:30:00"
    assert normalize_date("10-07", date(2026, 10, 6)) == date(2026, 10, 7)
    assert normalize_date("31-02", date(2026, 1, 1)) is None
    assert normalize_date("weekday:3", date(2026, 10, 6)) == date(2026, 10, 8)
    city = parse_utterance("Какие клиники есть в Москве?")
    assert normalize(city, clinic_id=None)["city"] == "moskva"


def test_response_engine_renders_backend_events_safely() -> None:
    result = AssistantResult(
        status="confirmation_required",
        code="CONFIRM_BOOKING",
        message="Sensitive backend detail",
    )
    reply = render_response(result)
    assert "Подтвердите" in reply
    assert "Sensitive" not in reply


def test_duplicate_template_branches_resolve_to_first_match() -> None:
    """confirmation_required/backend_error had dead duplicate branches (removed).

    The first (detailed) branch must win; the deleted generic texts must
    never appear.
    """
    confirm = render_event("confirmation_required", action="book", slot_label="10.10 в 09:00")
    assert "Записать вас" in confirm
    assert "выполнить это действие" not in confirm
    error = render_event("backend_error")
    assert "Ничего не изменено" in error
    assert "Попробуйте ещё раз позже" not in error


def test_all_template_events_render_without_leaks() -> None:
    events = [
        "greeting",
        "continue_dialogue",
        "clarification_required",
        "clarification_repeat",
        "patient_phone_required",
        "health_concern",
        "appointment_search_started",
        "appointment_slots_found",
        "appointment_slot_selected",
        "specialty_not_available",
        "confirmation_required",
        "appointment_status",
        "tenant_mismatch",
        "invalid_selection",
        "appointment_not_found",
        "patient_not_found",
        "patient_name_required",
        "invalid_patient_name",
        "no_doctors_found",
        "backend_error",
        "invalid_request",
        "workflow_cancelled",
        "already_completed",
        "need_clarification",
        "clinic_required",
        "date_required",
        "not_found",
        "conflict",
        "invalid_input",
        "unknown_request",
        "no_available_slots",
        "slots_found",
        "appointment_booked",
        "appointment_cancelled",
        "appointment_rescheduled",
        "appointment_completed",
        "patient_created",
        "no_clinics_found",
        "clinics_found",
        "appointments_found",
        "doctors_found",
        "doctor_found",
        "specialties_found",
        "request_succeeded",
    ]
    for event in events:
        text = render_event(event, message="fallback", prompt="подсказка")
        assert text, event
        assert "{{" not in text and "{%" not in text, event


def test_legacy_chat_response_contract_remains_typed() -> None:
    response = ChatResponse(conversation_id="abc", message="ok", model="deterministic")
    assert response.model == "deterministic"


async def test_booking_without_date_requests_clarification() -> None:
    chat_service.reset_conversations()
    response = await chat_service.chat(
        "Хочу записаться к кардиологу",
        "pipeline-test",
        session=cast(AsyncSession, None),
    )
    assert "дату" in response.message
    assert response.model == "deterministic"
    chat_service.reset_conversations()
