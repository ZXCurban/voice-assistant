"""AI chat layer tests (mocked LLM transport, no server needed)."""

import json

import httpx
import pytest

from app.ai.client import (
    LlmClient,
    LlmTimeoutError,
    LlmUnavailableError,
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


async def test_chat_endpoint_success_and_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ai import service as service_module
    from app.ai.client import LlmTimeoutError
    from app.ai.schemas import ChatResponse
    from app.core.identity import AssistantIdentity

    async def fake_chat(
        message: str,
        conversation_id: str | None = None,
        *,
        session: AsyncSession | None = None,
        redis: Redis | None = None,
        identity: AssistantIdentity | None = None,
        is_disconnected: object = None,
    ) -> ChatResponse:
        _ = (message, session, redis, identity, is_disconnected)
        return ChatResponse(conversation_id=conversation_id or "abc", message="hi", model="m")

    async def fake_down(
        message: str,
        conversation_id: str | None = None,
        *,
        session: AsyncSession | None = None,
        redis: Redis | None = None,
        identity: AssistantIdentity | None = None,
        is_disconnected: object = None,
    ) -> ChatResponse:
        _ = (message, conversation_id, session, redis, identity, is_disconnected)
        raise LlmUnavailableError()

    async def fake_slow(
        message: str,
        conversation_id: str | None = None,
        *,
        session: AsyncSession | None = None,
        redis: Redis | None = None,
        identity: AssistantIdentity | None = None,
        is_disconnected: object = None,
    ) -> ChatResponse:
        _ = (message, conversation_id, session, redis, identity, is_disconnected)
        raise LlmTimeoutError()

    app = create_app()
    monkeypatch.setattr(service_module, "chat", fake_chat)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "Здравствуйте"})
        assert response.status_code == 200
        assert response.json()["conversation_id"] == "abc"

        bad = await client.post("/api/v1/chat", json={"message": ""})
        assert bad.status_code == 422

    monkeypatch.setattr(service_module, "chat", fake_down)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        down = await client.post("/api/v1/chat", json={"message": "hi"})
        assert down.status_code == 502

    monkeypatch.setattr(service_module, "chat", fake_slow)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        slow = await client.post("/api/v1/chat", json={"message": "hi"})
        assert slow.status_code == 504
        assert "слишком долго" in slow.json()["detail"]
