"""Fastpath templates: instant RU replies, no LLM (pure, no DB)."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as chat_service
from app.ai.client import ChatCompletion, LlmClient, ToolCall
from app.ai.fastpath import match_fastpath
from app.assistant.schemas import AssistantContext


def test_greetings_match_case_insensitively() -> None:
    assert match_fastpath("привет") is not None
    assert match_fastpath("Здравствуйте!") is not None
    assert match_fastpath("  ДОБРЫЙ ВЕЧЕР... ") is not None
    assert "город" in (match_fastpath("привет") or "")


def test_who_and_help_templates() -> None:
    who = match_fastpath("кто ты?")
    assert who is not None and "консьерж" in who
    help_text = match_fastpath("что ты умеешь?")
    assert help_text is not None and "записать" in help_text
    assert match_fastpath("как записаться") is not None


def test_politeness_and_junk() -> None:
    assert match_fastpath("спасибо большое!") is not None
    assert match_fastpath("до свидания") is not None
    assert match_fastpath("???") is not None


def test_non_smalltalk_falls_through_to_llm() -> None:
    assert match_fastpath("у меня болит нога") is None
    assert match_fastpath("мне нужен дерматолог, ближайшее окно") is None
    assert match_fastpath("да") is None
    assert match_fastpath("нет, отмени") is None
    assert match_fastpath("") is None


def test_pending_confirmation_disables_fastpath() -> None:
    assert match_fastpath("привет", has_pending_confirmation=True) is None
    assert match_fastpath("спасибо", has_pending_confirmation=True) is None
    assert match_fastpath("привет", has_pending_confirmation=False) is not None


def test_e_normalization() -> None:
    assert match_fastpath("еще раз привет") is None  # not smalltalk anyway
    assert match_fastpath("здравствуйте") == match_fastpath("здравствуйте")


async def test_chat_fastpath_skips_llm(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """Smalltalk never touches the model: instant reply, history kept."""
    chat_service.reset_conversations()

    async def _boom(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        raise AssertionError("LLM must not be called for smalltalk")

    monkeypatch.setattr(LlmClient, "complete_with_tools", _boom)
    response = await chat_service.chat("привет", session=db_session)
    assert "город" in response.message
    history = chat_service._conversations[response.conversation_id]
    assert [m["role"] for m in history] == ["user", "assistant"]
    chat_service.reset_conversations()


async def test_chat_aborts_when_client_disconnected(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """Closed tab stops the loop before any LLM generation is spent."""
    chat_service.reset_conversations()

    async def _boom(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        raise AssertionError("LLM must not be called after disconnect")

    async def _gone() -> bool:
        return True

    monkeypatch.setattr(LlmClient, "complete_with_tools", _boom)
    response = await chat_service.chat("покажи клиники", session=db_session, is_disconnected=_gone)
    assert response.message  # fallback reply, no exception
    chat_service.reset_conversations()


def test_remembered_city_prefers_latest_message() -> None:
    context = AssistantContext(city="lisboa")
    history = [
        {"role": "user", "content": "я из Лиссабона"},
        {"role": "assistant", "content": "ок"},
        {"role": "user", "content": "вообще-то я в Варшаве"},
    ]
    assert chat_service._remembered_city(history, context) == "warszawa"
    assert context.city == "warszawa"


def test_remembered_city_falls_back_to_context() -> None:
    context = AssistantContext(city="warszawa")
    history = [{"role": "user", "content": "да, подходит"}]
    assert chat_service._remembered_city(history, context) == "warszawa"
    empty = AssistantContext()
    assert chat_service._remembered_city(history, empty) is None


async def test_chat_backfills_city_for_find_clinics(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """Model omitted city → dialogue layer injects it from user text."""
    chat_service.reset_conversations()
    seen: list[dict] = []

    async def _fake_complete(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        _ = (self, messages, max_tokens, temperature, tools)
        if not seen:
            return ChatCompletion(
                content="",
                tool_calls=[ToolCall(id="c1", name="find_clinics", arguments={}, raw={})],
            )
        return ChatCompletion(content="клиники: Варшава")

    async def _fake_execute(
        session: AsyncSession, name: str, arguments: dict, context: object = None
    ) -> dict:
        _ = (session, context)
        assert name == "find_clinics"
        seen.append(dict(arguments))
        return {
            "status": "success",
            "code": "OK",
            "details": {"clinics": [], "matched_city": "warszawa"},
        }

    monkeypatch.setattr(LlmClient, "complete_with_tools", _fake_complete)
    monkeypatch.setattr(chat_service, "execute_tool", _fake_execute)
    response = await chat_service.chat("я живу в Варшаве, болит нога", session=db_session)
    assert seen and seen[0].get("city") == "warszawa"
    assert response.message == "клиники: Варшава"
    context = chat_service._contexts[response.conversation_id]
    assert context.city == "warszawa"
    chat_service.reset_conversations()


async def test_chat_confirmation_answer_reaches_llm(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """«да» while a preview is pending must reach the LLM, not a template."""
    chat_service.reset_conversations()
    calls: list[bool] = []

    async def _fake(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        _ = (self, messages, max_tokens, temperature, tools)
        calls.append(True)
        return ChatCompletion(content="записал")

    monkeypatch.setattr(LlmClient, "complete_with_tools", _fake)
    first = await chat_service.chat("хочу записаться к дерматологу", session=db_session)
    chat_service._history(first.conversation_id).append(
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": json.dumps({"status": "confirmation_required", "code": "X"}),
        }
    )
    second = await chat_service.chat("да", first.conversation_id, session=db_session)
    assert calls and second.message == "записал"
    chat_service.reset_conversations()
