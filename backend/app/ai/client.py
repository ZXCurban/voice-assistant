"""Thin async client for llama-server's OpenAI-compatible HTTP API.

Only dependency is httpx (runtime). No SDKs, no model files here —
the server process owns the GGUF model.
"""

from dataclasses import dataclass

import httpx


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


class LlmClient:
    """POST /v1/chat/completions with explicit timeout and error mapping."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._timeout = httpx.Timeout(timeout_s)
        self._transport = transport

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Return the assistant reply text or raise LlmError."""
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            # Qwen3 is a hybrid reasoning model: without this flag all
            # tokens go to reasoning_content and the reply is empty.
            # Templates without this variable (e.g. Gemma) ignore it.
            "chat_template_kwargs": {"enable_thinking": False},
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

    @property
    def model(self) -> str:
        """Model name reported to API consumers (config value, informational)."""
        return self._model
