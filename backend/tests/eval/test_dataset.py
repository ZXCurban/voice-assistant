"""Dataset contract tests: schema, uniqueness, coverage, leakage."""

import json
from collections import Counter
from pathlib import Path

from app.eval.schemas import EvalItem
from app.services.frida_router import FRIDA_INTENTS

DATA = Path(__file__).parent / "data"
ALLOWED_CATEGORIES = {
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "appointment_status",
    "doctor_schedule",
    "doctor_information",
    "clinic_information",
    "operator",
    "unclear",
    "short",
    "long",
    "followup",
    "stt",
    "abbrev",
    "typos",
    "mixed",
    "ood",
    "sensitive",
    "contradictory",
    "unauthorized",
}


def _load(name: str) -> list[EvalItem]:
    rows = [
        EvalItem(**json.loads(line))
        for line in (DATA / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows, f"{name} is empty"
    return rows


def test_full_dataset_size_and_schema():
    rows = _load("clinic_intents_v1.jsonl")
    assert 300 <= len(rows) <= 500, len(rows)
    ids = [r.id for r in rows]
    assert len(set(ids)) == len(ids), "duplicate ids"
    texts = [r.text.strip() for r in rows]
    assert all(texts), "empty text found"
    assert len(set(texts)) == len(texts), "duplicate texts"
    for r in rows:
        assert r.expected_intent in FRIDA_INTENTS, r.id
        assert isinstance(r.expected_needs_human, bool), r.id
        assert isinstance(r.expected_needs_clarification, bool), r.id
        assert r.category in ALLOWED_CATEGORIES, r.id


def test_category_coverage():
    rows = _load("clinic_intents_v1.jsonl")
    counts = Counter(r.category for r in rows)
    missing = ALLOWED_CATEGORIES - set(counts)
    assert not missing, f"missing categories: {missing}"
    assert all(v >= 10 for v in counts.values()), dict(counts)


def test_difficult_cases_present():
    rows = _load("clinic_intents_v1.jsonl")
    by_cat = Counter(r.category for r in rows)
    for cat in ("stt", "followup", "ood", "sensitive", "unauthorized", "unclear", "mixed"):
        assert by_cat[cat] >= 10, cat


def test_splits_disjoint_and_cover_full():
    full = {r.id for r in _load("clinic_intents_v1.jsonl")}
    parts = {
        n: {r.id for r in _load(f"split_{n}.jsonl")} for n in ("calibration", "validation", "test")
    }
    assert sum(len(v) for v in parts.values()) == len(full)
    assert parts["calibration"] | parts["validation"] | parts["test"] == full
    assert not (
        parts["calibration"] & parts["validation"]
        or parts["calibration"] & parts["test"]
        or parts["validation"] & parts["test"]
    )
    # 60/20/20
    assert len(parts["calibration"]) == 240
    assert len(parts["validation"]) == 80
    assert len(parts["test"]) == 80


def test_v2_total_and_frozen_test_core():
    rows = _load("clinic_intents_v2.jsonl")
    assert len(rows) == 600, len(rows)
    ids = [r.id for r in rows]
    assert len(set(ids)) == len(ids)
    v1_test = {r.id for r in _load("split_test.jsonl")}
    v2_test = {r.id for r in _load("split_test_v2.jsonl")}
    assert v1_test <= v2_test, "frozen v1 test core must survive in v2 test"
    assert len(_load("split_calibration_v2.jsonl")) == 360
    assert len(_load("split_validation_v2.jsonl")) == 120
    assert len(v2_test) == 120
    with_ctx = [r for r in rows if r.context]
    assert len(with_ctx) >= 20, "follow-up items need explicit context"
