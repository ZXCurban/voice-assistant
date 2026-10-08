"""Dialogue manager on the real orchestrator + SQLite (scripted NLU).

The unit tests (tests/unit/test_dialogue.py) run the same scenarios on an
in-memory fake backend; here the contract is checked against the real
tools, slot math and booking rules.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as chat_service
from app.ai.nlu_chat import NluChatService, install_nlu_chat
from app.ai.tools import execute_tool
from app.dialogue import templates as t
from app.dialogue.manager import DialogueManager
from app.dialogue.state import DialogueState
from tests.integration.helpers import make_clinic
from tests.unit.dialogue_fakes import Script, ScriptedEngine

PATIENT_PHONE = "+10000000001"  # created by make_clinic

SCRIPT: Script = {
    "запишите к кардиологу": ("book_appointment", {"specialty": "cardiology"}),
    "в понедельник": ("unknown_request", {"date": "weekday:0"}),
    "Варшава": ("unknown_request", {"city": "warszawa"}),
    "самый ранний": ("select_option", {"selection": "earliest"}),
    "я уже был": ("unknown_request", {"patient_mode": "registered"}),
    "впервые": ("unknown_request", {"patient_mode": "new"}),
    "Анна Новак": ("unknown_request", {"full_name": "Анна Новак"}),
    "+48 600 100 200": ("unknown_request", {"phone": "+48600100200"}),
    "+1 000 000 0001": ("unknown_request", {"phone": PATIENT_PHONE}),
    "да": ("confirm", {}),
    "нет": ("reject", {}),
    "какие у меня записи": ("get_appointments", {}),
    "отмените запись": ("cancel_appointment", {}),
}


class Session:
    """One conversation against the real tools."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session
        self.state = DialogueState()
        self.manager = DialogueManager(ScriptedEngine(SCRIPT), self._execute)

    async def _execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await execute_tool(self.db, name, args, limit_slots=False)

    async def say(self, text: str) -> str:
        reply = await self.manager.handle(self.state, text)
        assert reply is not None
        return reply


async def _warsaw_clinic(db_session: AsyncSession) -> dict[str, int]:
    return dict(await make_clinic(db_session, name="NLU Warszawa", city="Warszawa"))


async def _to_slot_list(session: Session) -> str:
    assert await session.say("запишите к кардиологу") == t.ASK_DATE
    assert await session.say("в понедельник") == t.ask_city_spec("cardiology")
    return await session.say("Варшава")


async def test_registered_patient_books_through_the_real_orchestrator(
    db_session: AsyncSession,
) -> None:
    refs = await _warsaw_clinic(db_session)
    session = Session(db_session)
    listing = await _to_slot_list(session)
    assert listing.startswith("Доступное время: 1 — ")
    assert "в 09:00" in listing  # the doctor works 09:00-13:00 on Mondays
    assert await session.say("самый ранний") == t.ASK_PATIENT
    assert await session.say("я уже был") == t.ASK_PHONE
    question = await session.say("+1 000 000 0001")
    assert question.startswith("Записать вас на ") and "Jan Kowalski" in question

    # The preview must not have booked anything yet.
    before = await execute_tool(
        db_session,
        "get_appointments",
        {"clinic_id": refs["clinic_id"], "patient_id": refs["patient_id"]},
    )
    assert before["details"]["appointments"] == []

    assert (await session.say("да")).startswith("Готово, запись оформлена.")
    after = await execute_tool(
        db_session,
        "get_appointments",
        {"clinic_id": refs["clinic_id"], "patient_id": refs["patient_id"]},
    )
    assert len(after["details"]["appointments"]) == 1


async def test_new_patient_is_created_only_after_the_yes(db_session: AsyncSession) -> None:
    refs = await _warsaw_clinic(db_session)
    session = Session(db_session)
    await _to_slot_list(session)
    await session.say("самый ранний")
    assert await session.say("впервые") == t.ASK_NAME
    assert await session.say("Анна Новак") == t.ASK_PHONE
    question = await session.say("+48 600 100 200")
    assert question.startswith("Создать профиль «Анна Новак»")

    unknown = await execute_tool(
        db_session, "find_patient", {"clinic_id": refs["clinic_id"], "phone": "+48600100200"}
    )
    assert unknown["status"] == "not_found"

    assert (await session.say("да")).startswith("Готово, запись оформлена.")
    created = await execute_tool(
        db_session, "find_patient", {"clinic_id": refs["clinic_id"], "phone": "+48600100200"}
    )
    assert created["status"] == "success"
    assert created["details"]["patient"]["full_name"] == "Анна Новак"


async def test_declined_confirmation_books_nothing(db_session: AsyncSession) -> None:
    refs = await _warsaw_clinic(db_session)
    session = Session(db_session)
    await _to_slot_list(session)
    await session.say("самый ранний")
    await session.say("я уже был")
    await session.say("+1 000 000 0001")
    assert await session.say("нет") == t.WORKFLOW_CANCELLED
    listed = await execute_tool(
        db_session,
        "get_appointments",
        {"clinic_id": refs["clinic_id"], "patient_id": refs["patient_id"]},
    )
    assert listed["details"]["appointments"] == []


async def test_cancel_a_booked_appointment(db_session: AsyncSession) -> None:
    refs = await _warsaw_clinic(db_session)
    session = Session(db_session)
    await _to_slot_list(session)
    await session.say("самый ранний")
    await session.say("я уже был")
    await session.say("+1 000 000 0001")
    await session.say("да")

    other = Session(db_session)
    assert await other.say("отмените запись") == t.ASK_CITY
    assert await other.say("Варшава") == t.ASK_PHONE
    question = await other.say("+1 000 000 0001")
    assert question.startswith("Отменить запись на ")
    assert await other.say("да") == t.CANCELLED
    left = await execute_tool(
        db_session,
        "get_appointments",
        {
            "clinic_id": refs["clinic_id"],
            "patient_id": refs["patient_id"],
            "appointment_status": "booked",
        },
    )
    assert left["details"]["appointments"] == []


async def test_city_without_a_clinic_is_reported(db_session: AsyncSession) -> None:
    await _warsaw_clinic(db_session)
    script: Script = {**SCRIPT, "Лиссабон": ("unknown_request", {"city": "lisboa"})}
    session = Session(db_session)
    session.manager = DialogueManager(ScriptedEngine(script), session._execute)
    await session.say("запишите к кардиологу")
    await session.say("в понедельник")
    assert await session.say("Лиссабон") == t.CITY_NOT_SERVED


@pytest.fixture
async def nlu_service() -> AsyncIterator[NluChatService]:
    service = NluChatService(ScriptedEngine(SCRIPT))
    install_nlu_chat(service)
    yield service
    install_nlu_chat(None)
    chat_service.reset_conversations()


def test_chat_endpoint_uses_the_nlu_when_enabled(
    api_client: TestClient, nlu_service: NluChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NLU_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        first = api_client.post("/api/v1/chat", json={"message": "запишите к кардиологу"})
        assert first.status_code == 200
        body = first.json()
        assert body["message"] == t.ASK_DATE
        assert body["model"] == nlu_service.model_name
        second = api_client.post(
            "/api/v1/chat",
            json={"message": "в понедельник", "conversation_id": body["conversation_id"]},
        )
        assert second.json()["message"] == t.ask_city_spec("cardiology")
    finally:
        get_settings.cache_clear()


async def test_phone_lookup_ignores_spaces_and_dashes(db_session: AsyncSession) -> None:
    from app.schemas.patient import PatientCreate
    from app.services import patients as patients_service

    refs = await _warsaw_clinic(db_session)
    await patients_service.create_patient(
        db_session,
        PatientCreate(
            clinic_id=refs["clinic_id"], full_name="Demo Pacjent", phone="+48 700 000 001"
        ),
    )
    for spelling in ("+48700000001", "+48 700-000-001", "+48 (700) 000 001"):
        found = await patients_service.get_patient_by_phone(db_session, refs["clinic_id"], spelling)
        assert found.full_name == "Demo Pacjent"
    # …and a different number still does not match.
    result = await execute_tool(
        db_session, "find_patient", {"clinic_id": refs["clinic_id"], "phone": "+48700000002"}
    )
    assert result["status"] == "not_found"
