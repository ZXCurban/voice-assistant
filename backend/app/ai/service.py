"""Agentic chat service: LLM tool loop over AssistantOrchestrator.

Each turn the model may call backend tools (clinics, doctors, slots,
booking, …). Tool results come from the real services layer, so the
model presents facts instead of inventing them. History stays in process
memory; booking confirmation flows through the orchestrator's
confirmation_required results.
"""

import json
import logging
import time
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import build_client
from app.ai.fastpath import match_fastpath
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import ChatResponse
from app.ai.tools import TOOLS, execute_tool
from app.assistant.schemas import AssistantContext
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Max stored turns per conversation (user+assistant pairs), excluding the
# always-prepended system prompt. In-memory only: restarts drop history.
# Kept small so the dialogue fits the local model's context window.
MAX_TURNS = 5
# Safety caps for one user turn. Iterations cover the 7-day slot scan
# (one find_slots per day) plus the final answer.
MAX_TOOL_ITERATIONS = 8
# Backstop only: slot lists are already capped to 8 in execute_tool.
MAX_TOOL_RESULT_CHARS = 2500

_WEEKDAYS_RU = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}

_conversations: dict[str, list[dict[str, Any]]] = {}
# Sticky dialogue memory per conversation: ids resolved once (clinic,
# patient, doctor, slot) are reused as fallbacks, so the model cannot lose
# them between turns. Explicit tool arguments always win.
_contexts: dict[str, AssistantContext] = {}


def _context(conversation_id: str) -> AssistantContext:
    """Return live context for a conversation, creating it if needed."""
    context = _contexts.get(conversation_id)
    if context is None:
        context = AssistantContext()
        _contexts[conversation_id] = context
    return context


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _update_context(name: str, result: dict[str, Any], context: AssistantContext) -> None:
    """Fold successful tool results into sticky context (best effort)."""
    _ = name
    if result.get("status") not in ("success", "confirmation_required"):
        return
    details = result.get("details")
    if not isinstance(details, dict):
        return
    clinic_id = _as_int(details.get("clinic_id"))
    if clinic_id is not None:
        context.clinic_id = clinic_id
    slots = details.get("slots")
    if isinstance(slots, list) and slots and isinstance(slots[0], dict):
        first = slots[0]
        spec = first.get("specialty")
        if isinstance(spec, dict):
            spec_id = _as_int(spec.get("id"))
            if spec_id is not None:
                context.selected_specialty_id = spec_id
        doc = first.get("doctor")
        if isinstance(doc, dict):
            doc_id = _as_int(doc.get("id"))
            if doc_id is not None:
                context.selected_doctor_id = doc_id
    appointment = details.get("appointment")
    if isinstance(appointment, dict):
        patient = appointment.get("patient")
        if isinstance(patient, dict):
            patient_id = _as_int(patient.get("id"))
            if patient_id is not None:
                context.patient_id = patient_id
        starts_at = appointment.get("starts_at")
        if isinstance(starts_at, str):
            try:
                parsed = datetime.fromisoformat(starts_at)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                context.selected_slot = parsed
    patient = details.get("patient")
    if isinstance(patient, dict):
        patient_id = _as_int(patient.get("id"))
        if patient_id is not None:
            context.patient_id = patient_id


def _history(conversation_id: str) -> list[dict[str, Any]]:
    """Return live history list for a conversation, creating it if needed."""
    history = _conversations.get(conversation_id)
    if history is None:
        history = []
        _conversations[conversation_id] = history
    return history


def _has_pending_confirmation(history: list[dict[str, Any]]) -> bool:
    """True if the last tool result waits for an explicit «да/нет».

    Fastpath templates must not swallow confirmation answers, so callers
    check this before consulting match_fastpath().
    """
    for entry in reversed(history):
        if entry.get("role") == "tool":
            try:
                payload = json.loads(str(entry.get("content") or "{}"))
            except ValueError:
                return False
            if not isinstance(payload, dict):
                return False
            return payload.get("status") == "confirmation_required"
        if entry.get("role") == "assistant" and entry.get("tool_calls"):
            continue
        if entry.get("role") == "assistant":
            return False
    return False


def _trim(history: list[dict[str, Any]]) -> None:
    """Drop oldest turns beyond MAX_TURNS (in place)."""
    overflow = len(history) - MAX_TURNS * 2
    if overflow > 0:
        del history[:overflow]


def _system_prompt() -> str:
    """System prompt with today's date so the model resolves relative dates."""
    today = date.today()
    return (
        f"{SYSTEM_PROMPT}\n\nСегодня: {today.isoformat()} "
        f"({_WEEKDAYS_RU[today.weekday()]}). Вычисляй даты вида «завтра» и "
        "«на этой неделе» от сегодняшнего дня и передавай их в формате YYYY-MM-DD."
    )


async def chat(
    message: str, conversation_id: str | None = None, *, session: AsyncSession
) -> ChatResponse:
    """Run one user turn through the tool loop, return the final reply."""
    settings = get_settings()
    active_id = conversation_id or uuid.uuid4().hex
    history = _history(active_id)
    history.append({"role": "user", "content": message})
    _trim(history)

    # Deterministic smalltalk first: instant reply, zero LLM cost.
    # Never fires while a booking preview waits for «да/нет».
    fast = match_fastpath(message, has_pending_confirmation=_has_pending_confirmation(history))
    if fast is not None:
        history.append({"role": "assistant", "content": fast})
        return ChatResponse(conversation_id=active_id, message=fast, model=settings.llm_model)

    client = build_client(settings)
    reply = "Не получилось обработать запрос, попробуйте переформулировать, пожалуйста."
    for _ in range(MAX_TOOL_ITERATIONS):
        started = time.monotonic()
        completion = await client.complete_with_tools(
            messages=[{"role": "system", "content": _system_prompt()}, *history],
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            tools=TOOLS,
        )
        elapsed = time.monotonic() - started
        logger.info("llm turn took %.2fs (tools=%d)", elapsed, len(completion.tool_calls))
        if not completion.tool_calls:
            reply = completion.content.strip() or reply
            history.append({"role": "assistant", "content": reply})
            break
        history.append(
            {
                "role": "assistant",
                "content": completion.content,
                "tool_calls": [call.raw for call in completion.tool_calls],
            }
        )
        for call in completion.tool_calls:
            result = await execute_tool(
                session, call.name, call.arguments, context=_context(active_id)
            )
            _update_context(call.name, result, _context(active_id))
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS],
                }
            )
    else:
        history.append({"role": "assistant", "content": reply})
    return ChatResponse(conversation_id=active_id, message=reply, model=settings.llm_model)


def reset_conversations() -> None:
    """Drop all in-memory history (tests and local restarts)."""
    _conversations.clear()
    _contexts.clear()
