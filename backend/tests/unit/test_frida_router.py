"""Unit tests for the FRIDA adapter. Model calls are mocked — the real
weights are exercised only by scripts/run_eval.py, never here.
"""

import pytest

from app.services.frida_router import (
    FridaRouter,
    apply_policy,
    build_frida_request,
    build_frida_state,
    fallback_decision,
    parse_judge_response,
)


def _fake_judge(request: dict) -> dict:
    assert "state" in request and "questions" in request
    return {
        "answers": {
            "intent": {
                "type": "choice",
                "probabilities": {"book_appointment": 0.9, "unclear": 0.1},
                "confidence": 0.9,
                "choice": "book_appointment",
            },
            "needs_human": {"type": "noul", "noul": 0.05},
            "needs_clarification": {"type": "noul", "noul": 0.1},
        }
    }


def test_state_last_and_with_context():
    assert build_frida_state("  Хочу к врачу  ") == "Хочу к врачу"
    s = build_frida_state(
        "А в четверг?", previous_context="Хочу перенести запись", mode="with_context"
    )
    assert "перенести" in s and "четверг" in s
    assert build_frida_state("") == ""
    assert len(build_frida_state("x" * 5000)) <= 2000


def test_request_schema_has_three_questions():
    req = build_frida_request("тест")
    assert set(req["questions"]) == {"intent", "needs_human", "needs_clarification"}
    assert req["questions"]["intent"]["type"] == "choice"
    assert len(req["questions"]["intent"]["criteria"]) == 9


def test_decide_happy_path():
    d = FridaRouter(judge=_fake_judge, backend="mock").decide("Хочу записаться")
    assert d.intent == "book_appointment" and d.error is None
    assert d.confidence == pytest.approx(0.9) and d.backend == "mock"


def test_decide_no_judge_fallback():
    d = FridaRouter(judge=None).decide("текст")
    assert d.intent == "unclear" and d.error is not None


def test_decide_empty_state_fallback():
    d = FridaRouter(judge=_fake_judge).decide("   ")
    assert d.error == "empty-state"


def test_decide_exception_fallback():
    def boom(request: dict) -> dict:
        raise RuntimeError("gpu gone")

    d = FridaRouter(judge=boom).decide("текст")
    assert d.intent == "unclear" and d.error is not None and "RuntimeError" in d.error


def test_decide_timeout_fallback():
    import time as _t

    def slow(request: dict) -> dict:
        _t.sleep(2)
        return _fake_judge(request)

    d = FridaRouter(judge=slow).decide("текст", timeout_s=0.1)
    assert d.error == "timeout"


def test_parse_malformed_response_safe():
    d = parse_judge_response({"garbage": 1}, latency_ms=1.0, backend="mock")
    assert d.intent == "unclear" and d.error is not None


def test_parse_unknown_choice_maps_to_unclear():
    resp = {
        "answers": {"intent": {"choice": "fly_to_moon", "probabilities": {}, "confidence": 0.99}}
    }
    d = parse_judge_response(resp, latency_ms=1.0, backend="mock")
    assert d.intent == "unclear"


def test_policy_hint_clarify_handoff_fallback():
    d = FridaRouter(judge=_fake_judge, backend="mock").decide("x")
    assert apply_policy(d)["action"] == "hint"
    d.needs_human = 0.9
    assert apply_policy(d)["action"] == "handoff"
    d.needs_human = 0.0
    d.confidence = 0.2
    assert apply_policy(d)["action"] == "clarify"
    f = fallback_decision(reason="timeout")
    assert apply_policy(f)["action"] == "fallback"


def test_cyrillic_stt_typo_state_survives():
    s = build_frida_state("запишите к дерматолок, Ковальски")
    assert "дерматолок" in s
