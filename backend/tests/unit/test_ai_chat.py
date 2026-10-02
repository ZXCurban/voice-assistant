"""AI chat layer tests (mocked LLM transport, no server needed)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as chat_service
from app.ai.client import (
    ChatCompletion,
    LlmClient,
    LlmTimeoutError,
    LlmUnavailableError,
    ToolCall,
)
from app.main import create_app


def _ok_transport(captured: list[dict], text: str = "Здравствуйте!") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": text}}]},
        )

    return httpx.MockTransport(handler)


async def test_client_returns_reply_text() -> None:
    captured: list[dict] = []
    client = LlmClient(
        base_url="http://127.0.0.1:8080",
        model="test-model",
        timeout_s=5.0,
        transport=_ok_transport(captured, "Привет!"),
    )
    reply = await client.complete(
        messages=[{"role": "user", "content": "Здравствуйте"}],
        max_tokens=64,
        temperature=0.0,
    )
    assert reply == "Привет!"
    assert captured[0]["model"] == "test-model"
    assert captured[0]["messages"][0]["role"] == "user"


async def test_complete_with_tools_parses_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        assert payload["tool_choice"] == "auto"
        assert payload["tools"][0]["function"]["name"] == "find_slots"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "find_slots",
                                        "arguments": '{"clinic_id": 1}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = LlmClient(
        base_url="http://127.0.0.1:8080",
        model="m",
        timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )
    completion = await client.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        temperature=0.0,
        tools=[{"type": "function", "function": {"name": "find_slots"}}],
    )
    assert completion.tool_calls[0].name == "find_slots"
    assert completion.tool_calls[0].arguments == {"clinic_id": 1}
    assert completion.tool_calls[0].raw["id"] == "call_1"


async def test_complete_with_tools_bad_arguments_shape() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "x",
                            "tool_calls": [
                                {
                                    "id": "c9",
                                    "type": "function",
                                    "function": {"name": "find_slots", "arguments": "[1,2"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = LlmClient(
        base_url="http://127.0.0.1:8080",
        model="m",
        timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )
    completion = await client.complete_with_tools(
        messages=[], max_tokens=8, temperature=0.0, tools=[]
    )
    # Unparseable arguments degrade to {} so the executor reports invalid args.
    assert completion.tool_calls[0].arguments == {}


async def test_client_maps_connection_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = LlmClient(
        base_url="http://127.0.0.1:9",
        model="m",
        timeout_s=1.0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LlmUnavailableError):
        await client.complete(messages=[], max_tokens=8, temperature=0.0)


async def test_client_maps_bad_status_and_malformed_json() -> None:
    bad_status = LlmClient(
        base_url="http://x",
        model="m",
        timeout_s=1.0,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, json={})),
    )
    with pytest.raises(LlmUnavailableError):
        await bad_status.complete(messages=[], max_tokens=8, temperature=0.0)

    malformed = LlmClient(
        base_url="http://x",
        model="m",
        timeout_s=1.0,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"oops": 1})),
    )
    with pytest.raises(LlmUnavailableError):
        await malformed.complete(messages=[], max_tokens=8, temperature=0.0)


async def test_client_maps_timeout() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client = LlmClient(
        base_url="http://x",
        model="m",
        timeout_s=0.1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LlmTimeoutError):
        await client.complete(messages=[], max_tokens=8, temperature=0.0)


async def test_service_keeps_history_across_turns(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    chat_service.reset_conversations()
    captured: list[dict] = []

    async def fake_complete_with_tools(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        captured.append({"n_messages": len(messages)})
        _ = (self, max_tokens, temperature, tools)
        return ChatCompletion(content="ok")

    monkeypatch.setattr(LlmClient, "complete_with_tools", fake_complete_with_tools)
    first = await chat_service.chat("Мне нужен дерматолог", session=db_session)
    assert first.conversation_id
    second = await chat_service.chat(
        "Мне нужен невролог", first.conversation_id, session=db_session
    )
    assert second.conversation_id == first.conversation_id
    # system + user, assistant, user = 4 messages on the second turn.
    assert captured[0] == {"n_messages": 2}
    assert captured[1] == {"n_messages": 4}
    chat_service.reset_conversations()


async def test_service_tool_loop_executes_and_returns_text(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """Scripted model: tool call first, final text second."""
    chat_service.reset_conversations()
    calls: list[str] = []

    async def fake_complete_with_tools(
        self: LlmClient,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict],
    ) -> ChatCompletion:
        _ = (self, max_tokens, temperature, tools)
        if not calls:
            calls.append("tools")
            return ChatCompletion(
                content="",
                tool_calls=[ToolCall(id="c1", name="find_clinics", arguments={}, raw={})],
            )
        return ChatCompletion(content="Вот клиники.")

    async def fake_execute_tool(
        session: AsyncSession, name: str, arguments: dict, context: object = None
    ) -> dict:
        _ = (session, context)
        assert name == "find_clinics"
        assert arguments == {}
        return {"status": "success", "code": "OK", "details": {"clinics": []}}

    monkeypatch.setattr(LlmClient, "complete_with_tools", fake_complete_with_tools)
    monkeypatch.setattr(chat_service, "execute_tool", fake_execute_tool)
    response = await chat_service.chat("Где клиники?", session=db_session)
    assert response.message == "Вот клиники."
    history = chat_service._conversations[response.conversation_id]
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert history[2]["tool_call_id"] == "c1"
    chat_service.reset_conversations()


def test_chat_endpoint_success_and_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ai import service as service_module
    from app.ai.schemas import ChatResponse

    async def fake_chat(
        message: str,
        conversation_id: str | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> ChatResponse:
        _ = (message, session)
        return ChatResponse(conversation_id=conversation_id or "abc", message="hi", model="m")

    async def fake_down(
        message: str,
        conversation_id: str | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> ChatResponse:
        _ = (message, conversation_id, session)
        raise LlmUnavailableError()

    app = create_app()
    monkeypatch.setattr(service_module, "chat", fake_chat)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "Здравствуйте"})
        assert response.status_code == 200
        assert response.json()["conversation_id"] == "abc"

        bad = client.post("/api/v1/chat", json={"message": ""})
        assert bad.status_code == 422

    monkeypatch.setattr(service_module, "chat", fake_down)
    with TestClient(app) as client:
        down = client.post("/api/v1/chat", json={"message": "hi"})
        assert down.status_code == 502
