"""Text turn pipeline: parse, normalize, route to backend and render an event."""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.fastpath import match_fastpath
from app.ai.schemas import ChatResponse
from app.assistant import dialogue_log
from app.assistant.dialogue import DialogueState
from app.assistant.dialogue_manager import DialogueManager
from app.assistant.nlu import parse_utterance
from app.assistant.normalizer import normalize
from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantContext
from app.assistant.state_store import RedisDialogueStateStore
from app.core.config import get_settings
from app.core.identity import AssistantIdentity

logger = logging.getLogger(__name__)

_orchestrator = AssistantOrchestrator()
_dialogue_manager = DialogueManager(_orchestrator)

# Sticky dialogue memory per conversation: resolved clinic, patient, doctor
# and slot ids can be reused across turns. Explicit request fields win.
_contexts: dict[str, AssistantContext] = {}
_dialogue_states: dict[str, DialogueState] = {}


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


@asynccontextmanager
async def _conversation_state(
    active_id: str,
    redis: Redis | None,
    identity: AssistantIdentity,
    ttl_seconds: int,
) -> AsyncIterator[DialogueState]:
    if redis is None:
        state = _dialogue_states.setdefault(active_id, DialogueState(conversation_id=active_id))
        yield state
        return
    store = RedisDialogueStateStore(redis, ttl_seconds=ttl_seconds)
    async with store.conversation(
        active_id, subject=identity.subject, clinic_id=identity.clinic_id
    ) as state:
        yield state


async def chat(
    message: str,
    conversation_id: str | None = None,
    *,
    session: AsyncSession,
    redis: Redis | None = None,
    identity: AssistantIdentity | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> ChatResponse:
    """Run the deterministic pipeline and keep the existing chat API shape."""
    settings = get_settings()
    active_id = conversation_id or uuid.uuid4().hex
    principal = identity or AssistantIdentity(subject="local-demo")
    if is_disconnected is not None and await is_disconnected():
        return ChatResponse(
            conversation_id=active_id,
            message="Запрос остановлен.",
            model="deterministic",
        )
    async with _conversation_state(
        active_id, redis, principal, settings.conversation_state_ttl_s
    ) as state:
        if principal.tenant_locked:
            state.principal_id = principal.subject
            state.tenant_locked = True
            state.clinic_id = principal.clinic_id
            state.clinic_city = principal.clinic_city
            state.patient_id = principal.patient_id
            state.patient_phone = principal.verified_phone
            state.patient_full_name = principal.full_name
        first_turn = state.last_user_message_hash is None
        if first_turn and state.phase in {"START", "COMPLETED", "CANCELLED"}:
            fast = match_fastpath(message, has_pending_confirmation=False)
            if fast is not None:
                state.last_user_message_hash = sha256(message.encode()).hexdigest()
                state.last_response = fast
                state.touch()
                if dialogue_log.is_enabled():
                    dialogue_log.emit_turn(
                        dialogue_log.build_turn(
                            conversation_id=active_id,
                            user_message=message,
                            assistant_message=fast,
                            clinic_id=state.clinic_id,
                            phase=state.phase,
                            event="fastpath",
                        ),
                        sink_path=dialogue_log.configured_sink_path(),
                    )
                return ChatResponse(conversation_id=active_id, message=fast, model="deterministic")
        try:
            if settings.frida_enabled:
                import json

                parsed = parse_utterance(message)
                payload = normalize(parsed, clinic_id=state.clinic_id)
                decision = _frida_hint(json.dumps(payload, ensure_ascii=False))
                if decision == "clarify" and not state.pending_action:
                    from app.assistant.response import render_event

                    reply = render_event("clarification_required", missing=["request"])
                    if dialogue_log.is_enabled():
                        dialogue_log.emit_turn(
                            dialogue_log.build_turn(
                                conversation_id=active_id,
                                user_message=message,
                                assistant_message=reply,
                                clinic_id=state.clinic_id,
                                phase=state.phase,
                                event="clarification_required",
                                nlu=dialogue_log.nlu_from_parse(
                                    parsed.intent, parsed.confidence, parsed.slots
                                ),
                                normalized=dialogue_log.clean_payload(dict(payload)),
                            ),
                            sink_path=dialogue_log.configured_sink_path(),
                        )
                else:
                    reply = await _dialogue_manager.handle(session, state, message)
            else:
                reply = await _dialogue_manager.handle(session, state, message)
        except Exception as exc:
            logger.error("assistant dialogue failed (%s)", type(exc).__name__)
            state.phase = (
                "CONFIRMING"
                if state.pending_action is not None
                else "COLLECTING_DATA"
                if state.intent is not None
                else "ERROR"
            )
            reply = "Не удалось выполнить запрос. Ничего не изменено; попробуйте ещё раз."
            state.last_user_message_hash = sha256(message.encode()).hexdigest()
            state.last_response = reply
            state.touch()
            if dialogue_log.is_enabled() and not dialogue_log.turn_logged():
                # Errors inside DialogueManager are already logged there with
                # the collected NLU/actions; this covers pre-manager failures.
                dialogue_log.emit_turn(
                    dialogue_log.build_turn(
                        conversation_id=active_id,
                        user_message=message,
                        assistant_message=reply,
                        clinic_id=state.clinic_id,
                        phase=state.phase,
                        error=type(exc).__name__,
                    ),
                    sink_path=dialogue_log.configured_sink_path(),
                )
    return ChatResponse(conversation_id=active_id, message=reply, model="deterministic")


def reset_conversations() -> None:
    """Drop all in-memory history (tests and local restarts)."""
    _contexts.clear()
    _dialogue_states.clear()


_frida_router: Any = None


def _frida_hint(message: str) -> str:
    """Apply FRIDA confidence policy to a normalized JSON intent object.

    Observability: logs structured decision fields only — never the raw
    message, names, or phones.
    """
    global _frida_router
    settings = get_settings()
    try:
        from app.services.frida_router import (
            FridaRouter,
            apply_policy,
            build_frida_state,
            create_real_judge,
        )

        if _frida_router is None:
            _frida_router = FridaRouter(
                judge=create_real_judge(threads=settings.frida_threads),
                backend="onnx-int8",
            )
        state = build_frida_state(message)
        # OnnxJudge releases the GIL inside onnxruntime; direct call keeps
        # the diff minimal. decide() has its own timeout + fallback.
        decision = _frida_router.decide(state, timeout_s=settings.frida_timeout_s)
        policy = apply_policy(
            decision,
            intent_threshold=settings.frida_intent_threshold,
            clarify_threshold=settings.frida_clarify_threshold,
            human_threshold=settings.frida_human_threshold,
        )
        logger.info(
            "frida intent=%s conf=%.3f human=%.3f clar=%.3f ms=%.1f action=%s err=%s",
            decision.intent,
            decision.confidence,
            decision.needs_human,
            decision.needs_clarification,
            decision.latency_ms,
            policy["action"],
            decision.error,
        )
        if policy["action"] == "fallback":
            return "continue"
        if policy["action"] in {"clarify", "handoff"}:
            return "clarify"
        return str(decision.intent)
    except Exception as exc:  # FRIDA must never break chat
        logger.warning("frida hint skipped: %s", exc)
        return ""
