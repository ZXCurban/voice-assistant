"""FRIDA-Decisions adapter: bounded semantic decisions only.

This module is intentionally isolated: it prepares the state text,
constructs the typed questions, calls the model, and returns a normalized
decision object. It MUST NOT touch the database, call appointment
services, authorize users, or mutate anything.

Boundary (see docs/evaluation/frida-v1.md):
  FRIDA says  "looks like a reschedule (0.91), needs clarification (0.72)"
  Backend says "slot exists / belongs to patient / is free / tx committed"
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

FRIDA_MODEL_ID = "ai-forever/FRIDA-Decisions"
FRIDA_VERSION_PIN = "v0.3.0"

FRIDA_INTENTS: tuple[str, ...] = (
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "appointment_status",
    "doctor_schedule",
    "doctor_information",
    "clinic_information",
    "operator",
    "unclear",
)

INTENT_CRITERIA: dict[str, str] = {
    "book_appointment": "записаться на приём, найти время и врача, хочу попасть на приём",
    "reschedule_appointment": "перенести или переставить запись на другое время или дату",
    "cancel_appointment": "отменить или удалить запись, не придёт на приём",
    "appointment_status": "узнать когда приём, статус или детали существующей записи",
    "doctor_schedule": "расписание врача, когда принимает, часы и дни приёма",
    "doctor_information": "какой врач или специальность нужен, информация о врачах",
    "clinic_information": "адрес, телефон, часы клиники, как добраться, информация о клинике",
    "operator": (
        "оператор или живой человек, жалоба, экстренный случай, угроза, запрещённое действие"
    ),
    "unclear": "непонятно, слишком коротко, обрывок фразы или вопрос не по теме",
}

# Initial candidates only — real values are tuned on the validation split.
DEFAULT_INTENT_THRESHOLD = 0.85
DEFAULT_CLARIFY_THRESHOLD = 0.55
DEFAULT_HUMAN_THRESHOLD = 0.60

STATE_MAX_CHARS = 2000


def build_frida_state(
    text: str,
    *,
    previous_context: str | None = None,
    mode: str = "last",
) -> str:
    """Build the FRIDA state string. Pure, unit-testable.

    mode="last": only the last user message.
    mode="with_context": previous relevant context + last message.
    Never pass PII (names/phones) here — caller must redact first.
    """
    cleaned = " ".join((text or "").split())
    if mode == "with_context" and previous_context:
        ctx = " ".join(previous_context.split())
        combined = f"Контекст: {ctx} Реплика: {cleaned}"
        return combined[:STATE_MAX_CHARS]
    return cleaned[:STATE_MAX_CHARS]


def build_frida_request(state: str) -> dict[str, Any]:
    """Construct the typed request: intent(choice) + 2 noul guards."""
    return {
        "state": state,
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "Что хочет пациент? Выбери один вариант.",
                "criteria": dict(INTENT_CRITERIA),
            },
            "needs_human": {
                "type": "noul",
                "instructions": "Нужно ли передать диалог оператору?",
                "criteria": {
                    "true": (
                        "просьба о человеке, жалоба, экстренный случай, "
                        "угроза, запрещённое действие"
                    ),
                    "false": "обычный запрос о записи, расписании или информации",
                },
            },
            "needs_clarification": {
                "type": "noul",
                "instructions": "Не хватает ли данных для действия?",
                "criteria": {
                    "true": "нет врача, специальности, даты, слота или номера записи",
                    "false": "все нужные данные названы",
                },
            },
        },
    }


@dataclass
class FridaDecision:
    """Normalized decision. `error` set <=> fallback path was used."""

    intent: str
    intent_probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    needs_human: float = 0.0
    needs_clarification: float = 0.0
    latency_ms: float = 0.0
    backend: str = "unknown"
    error: str | None = None


class JudgeProtocol(Protocol):
    def __call__(self, request: dict[str, Any]) -> dict[str, Any]: ...


def parse_judge_response(
    response: dict[str, Any], *, latency_ms: float, backend: str
) -> FridaDecision:
    """Parse raw Judge/OnnxJudge output into FridaDecision (never raises)."""
    try:
        answers = response.get("answers", {})
        if not isinstance(answers, dict) or "intent" not in answers:
            return FridaDecision(
                intent="unclear",
                latency_ms=latency_ms,
                backend=backend,
                error="parse:missing-answers",
            )
        intent_a = answers.get("intent", {})
        probs = {k: float(v) for k, v in intent_a.get("probabilities", {}).items()}
        choice = str(intent_a.get("choice", "unclear"))
        if choice not in FRIDA_INTENTS:
            choice = "unclear"
        conf = float(intent_a.get("confidence", max(probs.values()) if probs else 0.0))
        nh = answers.get("needs_human", {})
        nc = answers.get("needs_clarification", {})
        return FridaDecision(
            intent=choice,
            intent_probabilities=probs,
            confidence=conf,
            needs_human=float(nh.get("noul", 0.0)),
            needs_clarification=float(nc.get("noul", 0.0)),
            latency_ms=latency_ms,
            backend=backend,
        )
    except Exception as exc:  # defensive: malformed model output -> safe fallback
        logger.warning("frida parse failed: %s", exc)
        return FridaDecision(
            intent="unclear", latency_ms=latency_ms, backend=backend, error=f"parse:{exc}"
        )


def fallback_decision(latency_ms: float = 0.0, reason: str = "fallback") -> FridaDecision:
    """Safe fallback: unclear + escalate-neutral, LLM-only path takes over."""
    return FridaDecision(intent="unclear", latency_ms=latency_ms, backend="fallback", error=reason)


class FridaRouter:
    """Thin wrapper around a Judge-compatible callable with timeout/fallback."""

    def __init__(self, judge: JudgeProtocol | None = None, *, backend: str = "onnx") -> None:
        self._judge = judge
        self._backend = backend

    def decide(self, state: str, *, timeout_s: float = 10.0) -> FridaDecision:
        import concurrent.futures

        if self._judge is None:
            return fallback_decision(reason="no-judge-configured")
        if not state.strip():
            return fallback_decision(reason="empty-state")
        request = build_frida_request(state)
        started = time.monotonic()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._judge, request)
                response = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            logger.warning("frida timeout after %.1fs", timeout_s)
            return fallback_decision(
                latency_ms=(time.monotonic() - started) * 1000, reason="timeout"
            )
        except Exception as exc:
            logger.warning("frida error: %s", exc)
            return fallback_decision(
                latency_ms=(time.monotonic() - started) * 1000, reason=f"error:{type(exc).__name__}"
            )
        latency_ms = (time.monotonic() - started) * 1000
        return parse_judge_response(response, latency_ms=latency_ms, backend=self._backend)


def apply_policy(
    decision: FridaDecision,
    *,
    intent_threshold: float = DEFAULT_INTENT_THRESHOLD,
    clarify_threshold: float = DEFAULT_CLARIFY_THRESHOLD,
    human_threshold: float = DEFAULT_HUMAN_THRESHOLD,
) -> dict[str, Any]:
    """Map a decision to a routing action. Pure function.

    Returns {"action": "hint"|"clarify"|"handoff"|"fallback", ...}.
    Thresholds are candidates — tune on validation, never on test.
    """
    if decision.error is not None:
        return {"action": "fallback", "reason": decision.error}
    if decision.needs_human >= human_threshold:
        return {"action": "handoff", "reason": "needs_human", "score": decision.needs_human}
    if decision.confidence < intent_threshold or decision.needs_clarification >= clarify_threshold:
        return {
            "action": "clarify",
            "reason": "low_confidence"
            if decision.confidence < intent_threshold
            else "needs_clarification",
            "intent_hint": decision.intent,
        }
    return {"action": "hint", "intent_hint": decision.intent, "confidence": decision.confidence}


def create_real_judge(*, threads: int = 4) -> JudgeProtocol:
    """Load the real OnnxJudge (CPU int8). Heavy: downloads ~1.2 GB once."""
    import importlib  # lazy: keeps unit tests dependency-free

    mod = importlib.import_module("frida_decisions")
    judge = mod.OnnxJudge.from_pretrained(FRIDA_MODEL_ID, threads=threads)
    return judge  # type: ignore[no-any-return]
