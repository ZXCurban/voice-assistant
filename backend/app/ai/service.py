"""Chat service: NLU dialogue manager and/or LLM tool loop over AssistantOrchestrator.

Order of a turn: fastpath smalltalk → NLU + dialogue manager (when
NLU_ENABLED and the models load) → LLM tool loop (default path, and the
fallback for turns the NLU cannot understand when NLU_LLM_FALLBACK is on).

LLM tool loop: each turn the model may call backend tools (clinics, doctors, slots,
booking, …). Tool results come from the real services layer, so the
model presents facts instead of inventing them. History stays in process
memory; booking confirmation flows through the orchestrator's
confirmation_required results.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import build_client
from app.ai.fastpath import match_fastpath
from app.ai.nlu_chat import get_nlu_chat, install_nlu_chat
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import ChatResponse
from app.ai.tools import TOOLS, execute_tool
from app.assistant.schemas import AssistantContext
from app.core.config import get_settings
from app.dialogue.manager import ToolExecutor
from app.services import geo as geo_service

logger = logging.getLogger(__name__)

# Max stored turns per conversation (user+assistant pairs), excluding the
# always-prepended system prompt. In-memory only: restarts drop history.
# Kept small so the dialogue fits the local model's context window.
MAX_TURNS = 5
# Safety cap for one user turn. Day-by-day slot scans run server-side inside
# find_nearest_slots (ONE tool call), so a turn never legitimately needs
# more: typically 1-3 calls (find_clinics + slots + book preview).
# Kept low because each iteration is a full local-model generation.
MAX_TOOL_ITERATIONS = 6
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
    matched_city = details.get("matched_city")
    if isinstance(matched_city, str) and matched_city:
        context.city = matched_city
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


def _remembered_city(history: list[dict[str, Any]], context: AssistantContext) -> str | None:
    """Best-known city query: latest user message wins, context is fallback.

    Deterministic backfill so find_clinics keeps the user's city even when
    the model forgets to pass it. The freshest mention updates the context.
    """
    for entry in reversed(history):
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        matched = geo_service.canonical_city(content)
        if matched is not None:
            context.city = matched
            return matched
        break  # only the latest user message counts, then context fallback
    return context.city


def _system_prompt() -> str:
    """System prompt with today's date so the model resolves relative dates."""
    today = date.today()
    return (
        f"{SYSTEM_PROMPT}\n\nСегодня: {today.isoformat()} "
        f"({_WEEKDAYS_RU[today.weekday()]}). Вычисляй даты вида «завтра» и "
        "«на этой неделе» от сегодняшнего дня и передавай их в формате YYYY-MM-DD."
    )


def _executor(session: AsyncSession) -> ToolExecutor:
    """Tool executor for the deterministic dialogue layer (full slot lists)."""

    async def run(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await execute_tool(session, name, arguments, limit_slots=False)

    return run


async def chat(
    message: str,
    conversation_id: str | None = None,
    *,
    session: AsyncSession,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> ChatResponse:
    """Run one user turn through the tool loop, return the final reply.

    `is_disconnected` (e.g. Starlette's request.is_disconnected) aborts the
    loop between iterations so a closed browser tab stops burning
    local-model generations.
    """
    settings = get_settings()
    active_id = conversation_id or uuid.uuid4().hex
    history = _history(active_id)
    history.append({"role": "user", "content": message})
    _trim(history)

    nlu = await get_nlu_chat(settings)

    # Deterministic smalltalk first: instant reply, zero LLM cost.
    # Never fires while a booking preview waits for «да/нет» (LLM path) or
    # while the NLU dialogue waits for an answer to its own question.
    waiting = _has_pending_confirmation(history) or (nlu is not None and nlu.in_dialogue(active_id))
    fast = match_fastpath(message, has_pending_confirmation=waiting)
    if fast is not None:
        history.append({"role": "assistant", "content": fast})
        if nlu is not None:
            nlu.note_reply(active_id, fast)
        return ChatResponse(conversation_id=active_id, message=fast, model=settings.llm_model)

    if nlu is not None:
        nlu_reply = await nlu.turn(active_id, message, _executor(session))
        if nlu_reply is not None:
            history.append({"role": "assistant", "content": nlu_reply})
            return ChatResponse(conversation_id=active_id, message=nlu_reply, model=nlu.model_name)

    client = build_client(settings)
    reply = "Не получилось обработать запрос, попробуйте переформулировать, пожалуйста."
    for _ in range(MAX_TOOL_ITERATIONS):
        if is_disconnected is not None and await is_disconnected():
            logger.info("client gone, aborting tool loop")
            break
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
            call_args = dict(call.arguments)
            if (
                call.name == "find_clinics"
                and "city" not in call_args
                and "address" not in call_args
            ):
                remembered = _remembered_city(history, _context(active_id))
                if remembered is not None:
                    call_args["city"] = remembered
            result = await execute_tool(session, call.name, call_args, context=_context(active_id))
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
    install_nlu_chat(None)
