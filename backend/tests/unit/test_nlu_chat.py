"""NluChatService: per-conversation dialogue state around the DialogueManager."""

from app.ai.nlu_chat import MAX_CONVERSATIONS, NluChatService
from app.dialogue import templates as t
from tests.unit.dialogue_fakes import FakeBackend, Script, ScriptedEngine

SCRIPT: Script = {
    "хочу записаться к кардиологу": ("book_appointment", {"specialty": "cardiology"}),
    "завтра": ("unknown_request", {"date": "tomorrow"}),
}


def _service(*, defer_unknown: bool = False) -> tuple[NluChatService, FakeBackend]:
    return NluChatService(ScriptedEngine(SCRIPT), defer_unknown=defer_unknown), FakeBackend()


async def test_conversations_have_independent_state() -> None:
    service, backend = _service()
    assert await service.turn("a", "хочу записаться к кардиологу", backend.execute) == t.ASK_DATE
    assert service.in_dialogue("a")
    assert not service.in_dialogue("b")
    # Conversation b never asked for a specialty, so «завтра» means nothing to it.
    assert await service.turn("b", "завтра", backend.execute) == t.NOT_UNDERSTOOD
    reply = await service.turn("a", "завтра", backend.execute)
    assert reply == t.ask_city_spec("cardiology")


async def test_model_name_carries_the_engine_version() -> None:
    service, _ = _service()
    assert service.model_name == "nlu:scripted"


async def test_note_reply_becomes_the_next_nlu_context() -> None:
    service, backend = _service()
    service.note_reply("a", "Здравствуйте!")
    await service.turn("a", "завтра", backend.execute)
    engine = service._engine
    assert isinstance(engine, ScriptedEngine)
    assert engine.contexts == ["Здравствуйте!"]


async def test_unintelligible_turn_is_deferred_only_when_deferral_is_on() -> None:
    service, backend = _service(defer_unknown=True)
    assert await service.turn("a", "абракадабра", backend.execute) is None
    plain, backend = _service(defer_unknown=False)
    assert await plain.turn("a", "абракадабра", backend.execute) == t.NOT_UNDERSTOOD


async def test_least_recently_used_conversations_are_dropped() -> None:
    service, backend = _service()
    await service.turn("first", "хочу записаться к кардиологу", backend.execute)
    for index in range(MAX_CONVERSATIONS):
        service.note_reply(f"c{index}", "x")
    assert not service.in_dialogue("first")


async def test_reset_forgets_everything() -> None:
    service, backend = _service()
    await service.turn("a", "хочу записаться к кардиологу", backend.execute)
    service.reset()
    assert not service.in_dialogue("a")
