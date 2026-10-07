"""Structured per-turn dialogue logging for dataset / NLU fine-tuning.

One JSON record is emitted per assistant turn (one user message → one reply),
in chronological order. A full dialogue is all records sharing
``conversation_id``, ordered by ``ts`` (and ``turn_index`` at export time).

Each record carries, when available:

- user / assistant messages;
- NLU intent, confidence and slots;
- normalized request values;
- every backend action (tool/intent) executed within the turn plus its
  result (status/code, redacted details);
- the rendered dialogue event and the workflow phase;
- an ``error`` marker when the turn failed (such turns should be filtered
  out of training data).

Secrets (passwords, tokens, API keys, …) are redacted by key name and by
value pattern. Names and phones are kept: they are legitimate NLU slots,
and this project uses synthetic data only (see README).

Sinks: records always go through the ``assistant.dialogue`` stdlib logger
as single-line JSON (captured by Docker logs / CI). When
``ASSISTANT_DIALOG_LOG_PATH`` is set, records are additionally appended to
that JSONL file (best effort — logging never breaks the chat path).

No new HTTP endpoints: export to a training dataset is done offline with
``scripts/export_dialogue_logs.py``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings

SCHEMA_VERSION = 1

logger = logging.getLogger("assistant.dialogue")

REDACTED = "***REDACTED***"

# Key names whose values must never reach the logs (case-insensitive,
# substring match so ``old_password`` / ``X-Api-Key`` are covered too).
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "pass",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "authorization",
        "auth",
        "session_token",
        "otp",
        "pin",
        "cvv",
        "card_number",
        "private_key",
    }
)

# Secret-looking values inside free text (kept narrow to avoid masking
# ordinary names/phones): bearer headers, sk- API keys, long hex blobs.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bxox[bap]-[A-Za-z0-9\-]{8,}\b"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)(['\"]?)[A-Za-z0-9\-._~+/=]{12,}\2"),
)

_MAX_STR_CHARS = 2000
_MAX_LIST_ITEMS = 50


def redact_text(value: str) -> str:
    """Mask secret-looking substrings inside free text."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _is_sensitive_key(name: str) -> bool:
    lowered = name.casefold()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact(obj: Any) -> Any:
    """Return a redacted copy: secrets by key name, secret patterns in text."""
    if isinstance(obj, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact(value)
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _truncate(obj: Any) -> Any:
    """Bound record size: long strings / lists get a ``_truncated`` marker."""
    if isinstance(obj, str):
        if len(obj) > _MAX_STR_CHARS:
            return obj[:_MAX_STR_CHARS] + f"...<_truncated:{len(obj)}chars>"
        return obj
    if isinstance(obj, Mapping):
        return {str(key): _truncate(value) for key, value in obj.items()}
    if isinstance(obj, list):
        if len(obj) > _MAX_LIST_ITEMS:
            kept = [_truncate(item) for item in obj[:_MAX_LIST_ITEMS]]
            kept.append({"_truncated": f"+{len(obj) - _MAX_LIST_ITEMS}more"})
            return kept
        return [_truncate(item) for item in obj]
    if isinstance(obj, tuple):
        return [_truncate(item) for item in obj]
    return obj


def to_jsonable(obj: Any) -> Any:
    """Convert arbitrary payloads (dates, datetimes, …) to JSON-safe data."""
    try:
        return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(obj)


def clean_payload(obj: Any) -> Any:
    """Redact secrets, bound size and ensure JSON-safety (in that order)."""
    return to_jsonable(_truncate(redact(obj)))


class TurnSlotLog(BaseModel):
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class TurnNluLog(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    slots: dict[str, TurnSlotLog] = Field(default_factory=dict)


class TurnActionLog(BaseModel):
    """One backend action (tool/intent) executed within the turn."""

    tool: str
    request: dict[str, Any] = Field(default_factory=dict)
    status: str
    code: str
    details: dict[str, Any] = Field(default_factory=dict)


class DialogueTurnLog(BaseModel):
    """One logged assistant turn; one JSONL line via :meth:`to_jsonl`."""

    schema_version: int = SCHEMA_VERSION
    ts: str
    conversation_id: str
    clinic_id: int | None = None
    phase: str | None = None
    user_message: str
    assistant_message: str = ""
    nlu: TurnNluLog | None = None
    normalized: dict[str, Any] = Field(default_factory=dict)
    actions: list[TurnActionLog] = Field(default_factory=list)
    event: str | None = None
    error: str | None = None

    def to_jsonl(self) -> str:
        return self.model_dump_json(exclude_none=False)

    def to_dataset_item(self, turn_index: int) -> dict[str, Any]:
        """Project the turn onto an NLU fine-tuning row."""
        slots = (
            {
                name: {"value": slot.value, "confidence": slot.confidence}
                for name, slot in self.nlu.slots.items()
            }
            if self.nlu is not None
            else {}
        )
        return {
            "text": self.user_message,
            "intent": self.nlu.intent if self.nlu is not None else "unknown_request",
            "confidence": self.nlu.confidence if self.nlu is not None else 0.0,
            "slots": slots,
            "assistant_text": self.assistant_message,
            "event": self.event,
            "conversation_id": self.conversation_id,
            "turn_index": turn_index,
            "ts": self.ts,
        }


class _TurnBuffer:
    """Per-turn scratch space; lives in a ContextVar (async-safe)."""

    __slots__ = ("nlu", "normalized", "actions", "event")

    def __init__(self) -> None:
        self.nlu: TurnNluLog | None = None
        self.normalized: dict[str, Any] = {}
        self.actions: list[TurnActionLog] = []
        self.event: str | None = None


_turn_buffer: contextvars.ContextVar[_TurnBuffer | None] = contextvars.ContextVar(
    "assistant_dialogue_turn", default=None
)

# Whether the current turn was already emitted (success or error). Reset by
# start_turn(); set by emit_turn(). Lets outer layers (chat service) skip
# duplicate error records. Turns are never nested, so no token restore needed.
_turn_logged: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "assistant_dialogue_turn_logged", default=False
)


def start_turn() -> contextvars.Token[_TurnBuffer | None]:
    """Open a fresh per-turn buffer; pair with :func:`reset_turn`."""
    _turn_logged.set(False)
    return _turn_buffer.set(_TurnBuffer())


def reset_turn(token: contextvars.Token[_TurnBuffer | None]) -> None:
    """Close the per-turn buffer opened by :func:`start_turn`."""
    _turn_buffer.reset(token)


def turn_logged() -> bool:
    """Return True when the current turn already has an emitted record."""
    return _turn_logged.get()


def is_enabled() -> bool:
    """Kill-switch for dialogue logging (default on)."""
    try:
        return get_settings().assistant_dialog_logging_enabled
    except Exception:  # noqa: BLE001 — logging must never break chat
        return True


def configured_sink_path() -> str | None:
    """Optional JSONL file sink; None when disabled or unconfigured."""
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 — logging must never break chat
        return None
    if not settings.assistant_dialog_logging_enabled:
        return None
    return settings.assistant_dialog_log_path


def nlu_from_parse(intent: str, confidence: float, slots: Mapping[str, Any]) -> TurnNluLog:
    """Build the log NLU record from a parser output (any slot shape)."""
    parsed_slots: dict[str, TurnSlotLog] = {}
    for name, item in slots.items():
        value = getattr(item, "value", item)
        item_confidence = getattr(item, "confidence", 1.0)
        try:
            conf = float(item_confidence)
        except (TypeError, ValueError):
            conf = 1.0
        parsed_slots[str(name)] = TurnSlotLog(value=str(value), confidence=min(max(conf, 0.0), 1.0))
    try:
        intent_confidence = min(max(float(confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        intent_confidence = 0.0
    return TurnNluLog(intent=str(intent), confidence=intent_confidence, slots=parsed_slots)


def _buffer() -> _TurnBuffer | None:
    return _turn_buffer.get()


def note_nlu(intent: str, confidence: float, slots: Mapping[str, Any]) -> None:
    """Record the NLU parse of the current turn (first parse wins).

    Later re-parses inside the same turn (e.g. synthetic ``confirm`` parses)
    are derived, not parsed from user text, so they are not training signal.
    """
    buf = _buffer()
    if buf is None or buf.nlu is not None:
        return
    buf.nlu = nlu_from_parse(intent, confidence, slots)


def note_normalized(normalized: Mapping[str, Any]) -> None:
    """Record the normalizer output for the current turn."""
    buf = _buffer()
    if buf is None:
        return
    cleaned = clean_payload(dict(normalized))
    buf.normalized = cleaned if isinstance(cleaned, dict) else {}


def note_action(payload: Mapping[str, Any], result: Any) -> None:
    """Record one backend action (tool/intent) plus its execution result."""
    buf = _buffer()
    if buf is None:
        return
    request = clean_payload(dict(payload))
    details = getattr(result, "details", {})
    details_cleaned = clean_payload(details if isinstance(details, dict) else {})
    buf.actions.append(
        TurnActionLog(
            tool=str(payload.get("intent", "unknown")),
            request=request if isinstance(request, dict) else {},
            status=str(getattr(result, "status", "unknown")),
            code=str(getattr(result, "code", "UNKNOWN")),
            details=details_cleaned if isinstance(details_cleaned, dict) else {},
        )
    )


def note_event(event: str) -> None:
    """Record the rendered dialogue event (last one wins — one reply/turn)."""
    buf = _buffer()
    if buf is None:
        return
    buf.event = str(event)


def build_turn(
    *,
    conversation_id: str,
    user_message: str,
    assistant_message: str = "",
    clinic_id: int | None = None,
    phase: str | None = None,
    event: str | None = None,
    error: str | None = None,
    nlu: TurnNluLog | None = None,
    normalized: dict[str, Any] | None = None,
) -> DialogueTurnLog:
    """Assemble the turn record from the buffer plus explicit turn context."""
    buf = _buffer()
    return DialogueTurnLog(
        ts=datetime.now(UTC).isoformat(),
        conversation_id=str(conversation_id),
        clinic_id=clinic_id,
        phase=phase,
        user_message=redact_text(str(user_message)),
        assistant_message=redact_text(str(assistant_message)),
        nlu=nlu if nlu is not None else (buf.nlu if buf is not None else None),
        normalized=(
            normalized if normalized is not None else (buf.normalized if buf is not None else {})
        ),
        actions=list(buf.actions) if buf is not None else [],
        event=event if event is not None else (buf.event if buf is not None else None),
        error=error,
    )


def _append_to_file(path: str, line: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.warning("assistant dialogue file sink failed (%s): %s", path, exc)


def emit_turn(record: DialogueTurnLog, *, sink_path: str | None = None) -> None:
    """Emit one JSONL record: stdlib log always, file append when configured.

    Best effort by design — logging must never break the chat path.
    """
    line = record.to_jsonl()
    try:
        logger.info("%s", line)
    except Exception as exc:  # noqa: BLE001 — logging must not raise
        logger.warning("assistant dialogue log emit failed: %s", exc)
    _turn_logged.set(True)
    if sink_path:
        _append_to_file(sink_path, line)


def parse_jsonl(lines: Iterator[str]) -> Iterator[DialogueTurnLog]:
    """Parse JSONL log lines, skipping blanks; raises on corrupt records."""
    for line in lines:
        stripped = line.strip()
        if stripped:
            yield DialogueTurnLog.model_validate_json(stripped)


def group_dialogues(records: list[DialogueTurnLog]) -> dict[str, list[DialogueTurnLog]]:
    """Group records by conversation, each dialogue in chronological order."""
    grouped: dict[str, list[DialogueTurnLog]] = {}
    for record in records:
        grouped.setdefault(record.conversation_id, []).append(record)
    for turns in grouped.values():
        turns.sort(key=lambda item: item.ts)
    return grouped


def to_dataset_items(
    records: list[DialogueTurnLog], *, drop_errors: bool = True
) -> list[dict[str, Any]]:
    """Project log records onto NLU fine-tuning rows (ordered per dialogue)."""
    items: list[dict[str, Any]] = []
    for turns in group_dialogues(records).values():
        for index, turn in enumerate(turns):
            if drop_errors and turn.error is not None:
                continue
            items.append(turn.to_dataset_item(index))
    return items
