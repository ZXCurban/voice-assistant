"""Thin async client for any OpenAI-compatible chat-completions HTTP API.

Works with llama-server, vLLM, Ollama and other gateways that speak the
same dialect. Only dependency is httpx (runtime). No SDKs, no model
files, no model-specific prompt tokens here — model selection is config
(app.core.config Settings), model quirks are explicit constructor flags.

To point the assistant at another model, change LLM_* env vars only:
prompts (app.ai.prompts), tools (app.ai.tools) and the orchestrator
contract (app.assistant.schemas) are model-neutral and move unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass
class LlmError(Exception):
    """Base LLM failure (→ 502)."""

    message: str = "LLM request failed"


@dataclass
class LlmUnavailableError(LlmError):
    """llama-server unreachable or returned an error status (→ 502)."""

    message: str = "LLM server unavailable"


@dataclass
class LlmTimeoutError(LlmError):
    """LLM did not answer within the configured timeout (→ 504)."""

    message: str = "LLM request timed out"


@dataclass
class ToolCall:
    """One parsed function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatCompletion:
    """Assistant turn: final text plus optional tool calls."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LlmClient:
    """POST /v1/chat/completions with explicit timeout and error mapping."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        enable_thinking: bool | None = None,
        tool_choice: str = "auto",
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._timeout = httpx.Timeout(timeout_s)
        self._transport = transport
        self._enable_thinking = enable_thinking
        self._tool_choice = tool_choice

    def _thinking_kwargs(self) -> dict[str, Any]:
        """Extra payload keys for hybrid reasoning models.

        Only Qwen3-style templates understand `enable_thinking`; every
        other template ignores the unknown variable, so emitting
        `{"enable_thinking": False}` by default is safe and keeps replies
        in `content` instead of `reasoning_content`. Pass
        enable_thinking=True (or None) to omit the flag.
        """
        if self._enable_thinking:
            return {}
        if self._enable_thinking is None:
            return {}
        return {"chat_template_kwargs": {"enable_thinking": False}}

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Return the assistant reply text or raise LlmError."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            **self._thinking_kwargs(),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise LlmUnavailableError() from exc
        if response.status_code != 200:
            raise LlmUnavailableError(f"LLM server returned status {response.status_code}")
        try:
            return str(response.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmUnavailableError("LLM server returned malformed JSON") from exc

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | None = None,
    ) -> ChatCompletion:
        """Chat turn with function calling. Raises LlmError like complete()."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            "tool_choice": tool_choice or self._tool_choice,
            "tools": tools,
            **self._thinking_kwargs(),
        }
        message = await self._post(payload)
        content = _message_text(message)
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LlmUnavailableError("LLM server returned malformed tool_calls")
        for raw in raw_calls:
            if not isinstance(raw, dict):
                raise LlmUnavailableError("LLM server returned malformed tool_calls")
            fn = raw.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if not isinstance(arguments, dict):
                    arguments = {}
            except (TypeError, ValueError):
                arguments = {}
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or ""),
                    name=str(name),
                    arguments=arguments,
                    raw={
                        "id": str(raw.get("id") or ""),
                        "type": "function",
                        "function": {"name": str(name), "arguments": json.dumps(arguments)},
                    },
                )
            )
        return ChatCompletion(content=str(content), tool_calls=calls)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST the payload, return the first choice message as a dict."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise LlmUnavailableError() from exc
        if response.status_code != 200:
            raise LlmUnavailableError(f"LLM server returned status {response.status_code}")
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmUnavailableError("LLM server returned malformed JSON") from exc
        if not isinstance(message, dict):
            raise LlmUnavailableError("LLM server returned malformed JSON")
        return message

    @property
    def model(self) -> str:
        """Model name reported to API consumers (config value, informational)."""
        return self._model


def _message_text(message: dict[str, Any]) -> str:
    """Extract reply text across providers (str or OpenAI content-parts list)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(str(part["text"]))
        return "".join(parts)
    return ""


def build_client(settings: Settings) -> LlmClient:
    """Build the chat client purely from config (no hardcoded model).

    Swapping models = changing LLM_* env vars. When enable_thinking is
    False we send the Qwen3-style disable flag (harmless for other
    templates); when True we omit it so reasoning models can think.
    """
    return LlmClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        enable_thinking=settings.llm_enable_thinking,
        tool_choice=settings.llm_tool_choice,
    )
