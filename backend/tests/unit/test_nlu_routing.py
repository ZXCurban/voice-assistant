"""Who answers a chat turn: the trained NLU or the rule-based pipeline."""

from collections.abc import Iterator
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as chat_service
from app.ai.nlu_chat import NluChatService, install_nlu_chat
from app.core.config import get_settings
from app.core.identity import AssistantIdentity
from app.dialogue import templates as t
from tests.unit.dialogue_fakes import FakeBackend, Script, ScriptedEngine

SCRIPT: Script = {
    "хочу записаться к кардиологу": ("book_appointment", {"specialty": "cardiology"}),
    "завтра": ("unknown_request", {"date": "tomorrow"}),
}
SESSION = cast(AsyncSession, None)  # the rule-based pipeline fails before any query


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedEngine]:
    monkeypatch.setenv("NLU_ENABLED", "true")
    get_settings.cache_clear()
    chat_service.reset_conversations()
    backend = FakeBackend()

    async def execute(
        _session: Any, name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return await backend.execute(name, arguments)

    monkeypatch.setattr(chat_service, "execute_tool", execute)
    scripted = ScriptedEngine(SCRIPT)
    install_nlu_chat(NluChatService(scripted, defer_unknown=True))
    yield scripted
    chat_service.reset_conversations()
    get_settings.cache_clear()


async def test_nlu_answers_and_names_itself(engine: ScriptedEngine) -> None:
    reply = await chat_service.chat("хочу записаться к кардиологу", "a", session=SESSION)
    assert reply.message.startswith(t.ASK_DATE)
    assert reply.model == "nlu:scripted"
    follow_up = await chat_service.chat("завтра", "a", session=SESSION)
    assert follow_up.model == "nlu:scripted"


async def test_unconfident_turn_goes_to_the_rule_based_pipeline(engine: ScriptedEngine) -> None:
    # No specialty/date/city in the text: the scripted model is unconfident and
    # the deterministic fallback extractors find nothing, so the turn is
    # handed to the rule-based pipeline (which asks for the specialty).
    first = await chat_service.chat("хочу записаться", "b", session=SESSION)
    assert first.model == "deterministic"
    assert "специалисту" in first.message
    asked = len(engine.contexts)
    # The rule-based dialogue is now in progress: it keeps the turn, the NLU is not asked.
    second = await chat_service.chat("завтра", "b", session=SESSION)
    assert second.model == "deterministic"
    assert len(engine.contexts) == asked


async def test_tenant_locked_identity_never_uses_the_nlu(engine: ScriptedEngine) -> None:
    locked = AssistantIdentity(subject="p1", clinic_id=1, clinic_city="Москва", tenant_locked=True)
    reply = await chat_service.chat(
        "хочу записаться к кардиологу", "c", session=SESSION, identity=locked
    )
    assert reply.model == "deterministic"
    assert engine.contexts == []


async def test_fastpath_greeting_feeds_the_next_nlu_context(engine: ScriptedEngine) -> None:
    greeting = await chat_service.chat("привет", "d", session=SESSION)
    assert greeting.model == "deterministic"
    await chat_service.chat("хочу записаться к кардиологу", "d", session=SESSION)
    assert engine.contexts == [greeting.message]


async def test_fastpath_identity_works_beyond_the_first_turn(
    engine: ScriptedEngine,
) -> None:
    # «ты кто» mid-dialogue introduces the bot instead of confusing the NLU.
    await chat_service.chat("хочу записаться к кардиологу", "e", session=SESSION)
    await chat_service.chat("завтра", "e", session=SESSION)
    reply = await chat_service.chat("ты кто", "e", session=SESSION)
    assert reply.model == "deterministic"
    assert "консьерж" in reply.message
    # ...and the NLU dialogue resumes on the next turn with synced context.
    follow_up = await chat_service.chat("в Москве", "e", session=SESSION)
    assert follow_up.model == "nlu:scripted"
