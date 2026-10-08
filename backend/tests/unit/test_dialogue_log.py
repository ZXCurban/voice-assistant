"""Dialogue logging tests: redaction, turn records, file sink, export (no infra)."""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant import dialogue_log
from app.assistant.dialogue import DialogueState
from app.assistant.dialogue_manager import DialogueManager
from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantRequest, AssistantResult


class _LoggingBackend(AssistantOrchestrator):
    def __init__(self, *, fail_slots: bool = False) -> None:
        self.fail_slots = fail_slots

    async def handle(self, session: AsyncSession, request: AssistantRequest) -> AssistantResult:
        _ = session
        if request.intent == "find_specialties":
            return AssistantResult(
                status="success",
                code="OK",
                message="found",
                details={"specialties": [{"id": 17, "name": "cardiology"}]},
            )
        if request.intent in {"find_slots", "find_nearest_slots"}:
            if self.fail_slots:
                raise RuntimeError("slot engine boom")
            result_date = request.date or date.today() + timedelta(days=1)
            return AssistantResult(
                status="success",
                code="OK",
                message="slots",
                details={
                    "slots": [
                        {
                            "starts_at": f"{result_date.isoformat()}T14:00:00+00:00",
                            "doctor": {"id": 23, "full_name": "Ivan Petrov"},
                        }
                    ],
                    "date": result_date.isoformat(),
                    "timezone": "UTC",
                },
            )
        if request.intent == "find_patient":
            return AssistantResult(
                status="not_found", code="PATIENT_NOT_FOUND", message="patient not found"
            )
        if request.intent == "book_appointment" and not request.confirmed:
            return AssistantResult(
                status="confirmation_required", code="CONFIRM_BOOKING", message="confirm"
            )
        raise AssertionError(f"Unexpected backend action: {request.intent}")


def _logged_turns(caplog: pytest.LogCaptureFixture) -> list[dialogue_log.DialogueTurnLog]:
    turns: list[dialogue_log.DialogueTurnLog] = []
    for record in caplog.records:
        if record.name == "assistant.dialogue":
            turns.append(dialogue_log.DialogueTurnLog.model_validate_json(record.getMessage()))
    return turns


def test_secrets_redacted_but_slots_kept() -> None:
    payload = {
        "intent": "book_appointment",
        "password": "hunter2",
        "nested": {"api_key": "key-123", "phone": "+79990001122"},
        "note": "call Bearer abcdefgh1234 or sk-abcdef123456",
    }
    cleaned = dialogue_log.clean_payload(payload)
    assert isinstance(cleaned, dict)
    assert cleaned["password"] == dialogue_log.REDACTED
    assert cleaned["nested"]["api_key"] == dialogue_log.REDACTED
    assert cleaned["nested"]["phone"] == "+79990001122"
    assert "abcdefgh1234" not in str(cleaned)
    assert "sk-abcdef123456" not in str(cleaned)
    # JSON-safety: dates become strings.
    assert dialogue_log.clean_payload({"date": date(2026, 1, 2)}) == {"date": "2026-01-02"}


class FakeSlot:
    def __init__(self, value: str, confidence: float) -> None:
        self.value = value
        self.confidence = confidence


def test_turn_jsonl_roundtrip_and_dataset_projection() -> None:
    record = dialogue_log.DialogueTurnLog(
        ts="2026-10-07T10:00:00+00:00",
        conversation_id="conv-1",
        clinic_id=3,
        phase="SELECTING",
        user_message="Хочу записаться к кардиологу завтра",
        assistant_message="Доступное время: ...",
        nlu=dialogue_log.nlu_from_parse(
            "book_appointment", 0.88, {"specialty": FakeSlot("cardiology", 0.94)}
        ),
        normalized={"intent": "find_nearest_slots", "clinic_id": 3},
        actions=[
            dialogue_log.TurnActionLog(
                tool="find_nearest_slots",
                request={"intent": "find_nearest_slots"},
                status="success",
                code="OK",
            )
        ],
        event="appointment_slots_found",
    )
    line = record.to_jsonl()
    assert json.loads(line)["conversation_id"] == "conv-1"
    parsed = dialogue_log.DialogueTurnLog.model_validate_json(line)
    assert parsed == record
    item = parsed.to_dataset_item(turn_index=2)
    assert item["text"] == "Хочу записаться к кардиологу завтра"
    assert item["intent"] == "book_appointment"
    assert item["slots"]["specialty"] == {"value": "cardiology", "confidence": 0.94}
    assert item["turn_index"] == 2


async def test_manager_emits_ordered_turn_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="assistant.dialogue")
    manager = DialogueManager(_LoggingBackend())
    state = DialogueState(conversation_id="log-conv", clinic_id=3, patient_id=41)
    session = cast(AsyncSession, None)

    await manager.handle(session, state, "Хочу записаться к кардиологу.")
    await manager.handle(session, state, "Завтра после двух.")

    turns = _logged_turns(caplog)
    assert len(turns) == 2
    assert [turn.conversation_id for turn in turns] == ["log-conv", "log-conv"]
    assert turns[0].ts <= turns[1].ts
    assert turns[0].user_message == "Хочу записаться к кардиологу."
    assert turns[1].user_message == "Завтра после двух."
    assert all(turn.assistant_message for turn in turns)
    assert all(turn.event for turn in turns)
    assert all(turn.nlu is not None for turn in turns)
    slot_turn = next(turn for turn in turns if turn.actions)
    tools = [action.tool for action in slot_turn.actions]
    assert "find_slots" in tools or "find_nearest_slots" in tools
    assert {action.status for action in slot_turn.actions} <= {"success", "confirmation_required"}


async def test_error_turn_carries_error_marker(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="assistant.dialogue")
    manager = DialogueManager(_LoggingBackend(fail_slots=True))
    state = DialogueState(
        conversation_id="log-err",
        clinic_id=3,
        intent="book_appointment",
        specialty_name="cardiology",
        specialty_id=17,
        date=date.today() + timedelta(days=1),
        patient_id=41,
    )
    with pytest.raises(RuntimeError, match="slot engine boom"):
        await manager.handle(cast(AsyncSession, None), state, "Завтра")
    turns = _logged_turns(caplog)
    assert len(turns) == 1
    assert turns[0].error == "RuntimeError"
    assert turns[0].user_message == "Завтра"


async def test_logging_disabled_emits_nothing(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="assistant.dialogue")
    monkeypatch.setattr(dialogue_log, "is_enabled", lambda: False)
    manager = DialogueManager(_LoggingBackend())
    state = DialogueState(conversation_id="log-off", clinic_id=3)
    await manager.handle(cast(AsyncSession, None), state, "Привет")
    assert _logged_turns(caplog) == []


def test_file_sink_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "dialogues.jsonl"
    record = dialogue_log.build_turn(
        conversation_id="file-conv", user_message="hi", assistant_message="hello"
    )
    dialogue_log.emit_turn(record, sink_path=str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert dialogue_log.DialogueTurnLog.model_validate_json(lines[0]).conversation_id == (
        "file-conv"
    )


def test_group_and_export_helpers() -> None:
    first = dialogue_log.build_turn(conversation_id="c1", user_message="a", assistant_message="b")
    second = dialogue_log.build_turn(
        conversation_id="c1", user_message="c", assistant_message="d", error="RuntimeError"
    )
    other = dialogue_log.build_turn(conversation_id="c2", user_message="e", assistant_message="f")
    grouped = dialogue_log.group_dialogues([other, second, first])
    assert set(grouped) == {"c1", "c2"}
    assert [turn.user_message for turn in grouped["c1"]] == ["a", "c"]
    items = dialogue_log.to_dataset_items([other, second, first])
    assert [(item["conversation_id"], item["turn_index"]) for item in items] == [
        ("c2", 0),
        ("c1", 0),
    ]
    kept = dialogue_log.to_dataset_items([second], drop_errors=False)
    assert len(kept) == 1 and kept[0]["text"] == "c"


def test_parse_log_line_accepts_pure_and_prefixed_lines() -> None:
    record = dialogue_log.build_turn(
        conversation_id="conv-9",
        user_message="Хочу записаться к кардиологу",
        assistant_message="На какую дату?",
    )
    pure = record.to_jsonl()
    prefixed = "2026-10-07 08:00:21,080 INFO [assistant.dialogue] " + pure
    uvicorn_prefixed = "INFO:     " + pure
    assert dialogue_log.parse_log_line(pure) == record
    assert dialogue_log.parse_log_line(prefixed) == record
    assert dialogue_log.parse_log_line(uvicorn_prefixed) == record


def test_parse_log_line_ignores_blanks_and_unrelated_lines() -> None:
    assert dialogue_log.parse_log_line("") is None
    assert dialogue_log.parse_log_line("   ") is None
    assert dialogue_log.parse_log_line("INFO:     127.0.0.1 - GET /health 200 OK") is None
    assert (
        dialogue_log.parse_log_line("2026-10-07 INFO [uvicorn] Application startup complete.")
        is None
    )


def test_parse_log_line_raises_on_corrupt_record() -> None:
    with pytest.raises(ValueError):
        dialogue_log.parse_log_line(
            '2026-10-07 INFO [assistant.dialogue] {"schema_version": 1, "broken"'
        )
