"""Eval metrics: intent accuracy/macro-F1, routing/tool, escalation,
clarification, reliability, latency, calibration (ECE). No numpy/scipy
dependency — stdlib only so CI stays light.
"""

from __future__ import annotations

import math
from typing import Any

from app.eval.schemas import EvalItem, EvalPrediction


def _prf(y_true: list[str], y_pred: list[str], label: str) -> tuple[float, float, float, int]:
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p != label)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    support = sum(1 for t in y_true if t == label)
    return prec, rec, f1, support


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def compute_metrics(items: list[EvalItem], preds: list[EvalPrediction]) -> dict[str, Any]:
    """Compute the full metric set. Items and preds joined by id."""
    by_id = {p.id: p for p in preds}
    pairs = [(it, by_id[it.id]) for it in items if it.id in by_id]
    n = len(pairs)
    if not n:
        return {"n": 0}

    y_int = [it.expected_intent for it, _ in pairs]
    p_int = [p.predicted_intent for _, p in pairs]
    labels = sorted(set(y_int) | set(p_int))
    intent_acc = sum(1 for t, p in zip(y_int, p_int, strict=True) if t == p) / n

    per_class: dict[str, dict[str, float]] = {}
    f1s = []
    for lab in labels:
        prec, rec, f1, sup = _prf(y_int, p_int, lab)
        per_class[lab] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": sup,
        }
        f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    confusion: dict[str, dict[str, int]] = {t: {} for t in sorted(set(y_int))}
    for t, p in zip(y_int, p_int, strict=True):
        confusion[t][p] = confusion[t].get(p, 0) + 1

    # workflow / tool
    wf_pairs = [(it.expected_workflow, p.predicted_workflow) for it, p in pairs]
    workflow_acc = sum(1 for t, p in wf_pairs if t == p and t) / n
    tool_pairs = [(it.expected_tool, p.predicted_tool) for it, p in pairs if it.expected_tool]
    tool_acc = (sum(1 for t, p in tool_pairs if t == p) / len(tool_pairs)) if tool_pairs else None

    # escalation / clarification
    def _bin_stats(exp: list[bool], pred: list[bool]) -> dict[str, float]:
        fp = sum(1 for e, p in zip(exp, pred, strict=True) if not e and p)
        fn = sum(1 for e, p in zip(exp, pred, strict=True) if e and not p)
        tp = sum(1 for e, p in zip(exp, pred, strict=True) if e and p)
        tn = sum(1 for e, p in zip(exp, pred, strict=True) if not e and not p)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        return {
            "false_escalation": fp,
            "missed": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "rate": round(sum(pred) / len(pred), 4) if pred else 0.0,
            "tn": tn,
            "tp": tp,
        }

    esc = _bin_stats(
        [it.expected_needs_human for it, _ in pairs], [p.predicted_needs_human for _, p in pairs]
    )
    cla = _bin_stats(
        [it.expected_needs_clarification for it, _ in pairs],
        [p.predicted_needs_clarification for _, p in pairs],
    )

    errors = [p for _, p in pairs if p.error]
    lat = [p.latency_ms for _, p in pairs]

    # calibration: 5 confidence bins, ECE over intent correctness
    bins: list[dict[str, Any]] = []
    ece = 0.0
    for b in range(5):
        lo, hi = b / 5, (b + 1) / 5
        sel = [
            (t == p, c)
            for (t, p, c) in zip(y_int, p_int, [q.confidence for _, q in pairs], strict=True)
            if lo <= c < hi or (hi == 1.0 and c == 1.0)
        ]
        if not sel:
            bins.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0, "acc": None, "mean_conf": None})
            continue
        acc = sum(1 for ok, _ in sel if ok) / len(sel)
        mc = sum(c for _, c in sel) / len(sel)
        ece += abs(acc - mc) * len(sel) / n
        bins.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": len(sel),
                "acc": round(acc, 4),
                "mean_conf": round(mc, 4),
            }
        )

    return {
        "n": n,
        "intent_accuracy": round(intent_acc, 4),
        "intent_macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion": confusion,
        "workflow_accuracy": round(workflow_acc, 4),
        "tool_accuracy": round(tool_acc, 4) if tool_acc is not None else None,
        "tool_n": len(tool_pairs),
        "escalation": esc,
        "clarification": cla,
        "error_count": len(errors),
        "error_rate": round(len(errors) / n, 4),
        "latency_ms": {
            "mean": round(sum(lat) / len(lat), 2) if lat else 0.0,
            "p50": round(_percentile(lat, 0.5), 2),
            "p95": round(_percentile(lat, 0.95), 2),
            "max": round(max(lat), 2) if lat else 0.0,
        },
        "ece": round(ece, 4),
        "calibration_bins": bins,
    }
