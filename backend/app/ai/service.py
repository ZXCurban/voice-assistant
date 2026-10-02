"""Conversational chat service (no DB, no tools wired yet).

Keeps per-conversation message history in process memory. Booking and
schedule operations will later be exposed to the LLM as tool calls routed
through AssistantOrchestrator (docs/voice-map.md) — the extension point
is ChatService.chat(), which is the single place that builds the message
list sent to the model.
"""

import uuid

from app.ai.client import LlmClient
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import ChatResponse
from app.core.config import get_settings

# Max stored turns per conversation (user+assistant pairs), excluding the
# always-prepended system prompt. In-memory only: restarts drop history.
MAX_TURNS = 10

_conversations: dict[str, list[dict[str, str]]] = {}


def _history(conversation_id: str) -> list[dict[str, str]]:
    """Return live history list for a conversation, creating it if needed."""
    history = _conversations.get(conversation_id)
    if history is None:
        history = []
        _conversations[conversation_id] = history
    return history


def _trim(history: list[dict[str, str]]) -> None:
    """Drop oldest turns beyond MAX_TURNS (in place)."""
    overflow = len(history) - MAX_TURNS * 2
    if overflow > 0:
        del history[:overflow]


async def chat(message: str, conversation_id: str | None = None) -> ChatResponse:
    """Append a user turn, query the local LLM, append and return its reply."""
    settings = get_settings()
    active_id = conversation_id or uuid.uuid4().hex
    history = _history(active_id)
    history.append({"role": "user", "content": message})
    _trim(history)

    client = LlmClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    reply = await client.complete(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
    )
    history.append({"role": "assistant", "content": reply})
    return ChatResponse(conversation_id=active_id, message=reply, model=settings.llm_model)


def reset_conversations() -> None:
    """Drop all in-memory history (tests and local restarts)."""
    _conversations.clear()
