"""Reproducible evaluation CLI.

Usage:
  python scripts/run_eval.py --model heuristic --split all
  python scripts/run_eval.py --model frida --split validation --limit 20
  python scripts/run_eval.py --model assisted --split test --thr-intent 0.8 --thr-human 0.6 --thr-clarify 0.55
  python scripts/run_eval.py --tune --split validation   # threshold grid search

Models:
  heuristic — keyword proxy of the current LLM-only tool-loop routing.
    NOT the real Qwen3 model: it approximates intent/tool selection so the
    harness, metrics, and A/B plumbing are testable without a GPU LLM
    server. A live-LLM run is tracked as follow-up (needs LLM_BASE_URL).
  frida     — real FRIDA-Decisions (OnnxJudge, CPU int8) standalone.
  assisted  — FRIDA decision + heuristic LLM: use FRIDA intent hint when
    policy says "hint", else fall back to heuristic.

Results: backend/tests/eval/results/<model>_<split>.jsonl + _summary.json
with seed/version/timestamp environment info.
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.eval.metrics import compute_metrics  # noqa: E402
from app.eval.schemas import EvalItem, EvalPrediction  # noqa: E402
from app.services.frida_router import (  # noqa: E402
    DEFAULT_CLARIFY_THRESHOLD,
    DEFAULT_HUMAN_THRESHOLD,
    DEFAULT_INTENT_THRESHOLD,
    FRIDA_VERSION_PIN,
    FridaRouter,
    apply_policy,
    build_frida_state,
    create_real_judge,
)

DATA_DIR = ROOT / "backend" / "tests" / "eval" / "data"
RESULTS_DIR = ROOT / "backend" / "tests" / "eval" / "results"
DATASET_VERSION = "clinic_intents_v1"

INTENT_TO_WORKFLOW = {
    "book_appointment": "booking",
    "reschedule_appointment": "reschedule",
    "cancel_appointment": "cancel",
    "appointment_status": "status_lookup",
    "doctor_schedule": "schedule_lookup",
    "doctor_information": "doctor_search",
    "clinic_information": "clinic_info",
    "operator": "handoff",
    "unclear": "clarify",
}

INTENT_TO_TOOL = {
    "book_appointment": "find_slots",
    "reschedule_appointment": "reschedule_appointment",
    "cancel_appointment": "cancel_appointment",
    "appointment_status": "get_appointments",
    "doctor_schedule": "find_slots",
    "doctor_information": "find_doctors",
    "clinic_information": "find_clinics",
    "operator": None,
    "unclear": None,
}

# ---- heuristic baseline (documented proxy, see module docstring) ----
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "operator",
        re.compile(
            r"оператор|живой человек|живой\b|администратор|руководств|жалоб|претензи|регистратур|экстрен|скорая|скорую|суицид|диагноз|таблетки|рецепт|больничн|справк|поддела|базе клиники|другого пациента|всех пациентов|чужую|соседк|результаты анализов|вне очереди|заплачу|дежурн",
            re.I,
        ),
    ),
    (
        "cancel_appointment",
        re.compile(
            r"отмен|аннулир|сними|удали.*запис|отказываюсь|не приду|передумал|сорри.*отмена|ок.*отмена|убер.*приём|убери.*приём",
            re.I,
        ),
    ),
    (
        "reschedule_appointment",
        re.compile(
            r"перенес|перестав|перепиш|перезапиш|сдви|поменя.*врем|другое время|другая дата|не отмен.*перенес|перенос",
            re.I,
        ),
    ),
    (
        "appointment_status",
        re.compile(
            r"когда.*при[её]м|статус|во сколько.*записан|напомн|на какое число|активна|в силе|номер записи|забы.*врач|предстоящ|во сколько.*мой|подтверди|приходило смс|куда.*подойти|не отменили|лечащий врач|сколько записей|напомните фамилию",
            re.I,
        ),
    ),
    (
        "doctor_schedule",
        re.compile(
            r"когда принимает|расписание|часы при[её]ма|в какие дни|по выходным|до скольки|со скольки|график|принимает.*(завтра|сегодня|суббот|воскресенье|понедельник|вечер)|вечерние часы|окна|отпуске",
            re.I,
        ),
    ),
    (
        "doctor_information",
        re.compile(
            r"какой врач|кто лечит|посоветуй|расскажи о|стаж|чем занимается|кто.*принимает|к кому|лечит|лучший|хорошего|отзывы|женщина|русскоговорящ|дежурный",
            re.I,
        ),
    ),
    (
        "clinic_information",
        re.compile(
            r"где.*клиник|адрес|как добраться|телефон|парковк|работаете|сколько стоит|мрт|узи|экг|страховк|пандус|без записи|ближайш|находится|сайт|анализы сдать|отдел|частная",
            re.I,
        ),
    ),
    (
        "unclear",
        re.compile(
            r"погода|айфон|анекдот|пицц|такси|курс доллара|переведи|матч|петь|сочинение|кафе|ты кто|сколько времени|приготовь|закажи|напиши|^(да|нет|ага|хорошо|может быть|короче|здравствуйте|при[её]м|помогите|вопрос есть|ну такое|э+й?|не знаю|это самое|по поводу|\?\?|утром|завтра\?|в пятницу)$",
            re.I,
        ),
    ),
]

_HUMAN_RE = re.compile(
    r"оператор|живой|жалоб|экстрен|скорая|скорую|суицид|температура 39|температура 40|болит грудь|"
    r"кровь|давление 200|тошнит.*голов|проглотил|беременной|сыпь.*опасно|диагноз|рецепт|"
    r"мужа|другого пациента|всех пациентов|чужую|соседк|больничн|справк|поддела|доступа? к базе|"
    r"вне очереди|передо мной|карте$|без осмотра",
    re.I,
)
_ENTITY_RE = re.compile(
    r"понедельник|вторник|сред[ау]|четверг|пятниц|суббот|воскресенье|завтра|сегодня|утро|вечер|"
    r"\d{1,2}[-./]\d{1,2}|терапевт|кардиолог|окулист|офтальмолог|дерматолог|невролог|невропатолог|"
    r"хирург|педиатр|лор\b|уролог|гинеколог|эндокринолог|аллерголог|стоматолог|психотерапевт|"
    r"ковальск|новак|петров|доктор|врач",
    re.I,
)


def heuristic_predict(text: str) -> tuple[str, bool, bool]:
    t = text.strip()
    for intent, rx in _RULES:
        if rx.search(t):
            break
    else:
        intent = (
            "book_appointment"
            if re.search(
                r"запис|запиши|при[её]м|нужен|нужна|хочу|к \w+ологу|к лору|терапевту|врачу", t, re.I
            )
            else "unclear"
        )
    needs_human = bool(_HUMAN_RE.search(t))
    has_entity = bool(_ENTITY_RE.search(t))
    needs_clar = (len(t) < 14 and intent != "operator") or (
        intent
        in (
            "book_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "appointment_status",
        )
        and not has_entity
    )
    if intent in ("operator",):
        needs_clar = False
    return intent, needs_human, needs_clar


def load_items(split: str) -> list[EvalItem]:
    if split == "all":
        path = DATA_DIR / f"{DATASET_VERSION}.jsonl"
    else:
        path = DATA_DIR / f"split_{split}.jsonl"
    return [
        EvalItem(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def predict_heuristic(item: EvalItem) -> EvalPrediction:
    started = time.monotonic()
    intent, nh, nc = heuristic_predict(item.text)
    return EvalPrediction(
        id=item.id,
        predicted_intent=intent,
        predicted_needs_human=nh,
        predicted_needs_clarification=nc,
        predicted_workflow=INTENT_TO_WORKFLOW[intent],
        predicted_tool=INTENT_TO_TOOL[intent],
        confidence=0.6,
        latency_ms=(time.monotonic() - started) * 1000,
        backend="heuristic-llm-proxy",
    )


def get_router() -> FridaRouter:
    return FridaRouter(judge=create_real_judge(), backend="onnx-int8")


def predict_frida(item: EvalItem, router: FridaRouter) -> tuple[EvalPrediction, Any]:
    from app.services.frida_router import FridaDecision  # local import for typing

    state = build_frida_state(item.text)
    decision: FridaDecision = router.decide(state)
    return (
        EvalPrediction(
            id=item.id,
            predicted_intent=decision.intent,
            predicted_needs_human=decision.needs_human >= DEFAULT_HUMAN_THRESHOLD,
            predicted_needs_clarification=decision.needs_clarification >= DEFAULT_CLARIFY_THRESHOLD
            or decision.confidence < DEFAULT_INTENT_THRESHOLD,
            predicted_workflow=INTENT_TO_WORKFLOW.get(decision.intent, "clarify"),
            predicted_tool=INTENT_TO_TOOL.get(decision.intent),
            confidence=decision.confidence,
            latency_ms=decision.latency_ms,
            backend=decision.backend,
            error=decision.error,
        ),
        decision,
    )


def run(
    model: str, split: str, limit: int | None, thr_i: float, thr_h: float, thr_c: float
) -> dict:
    items = load_items(split)
    if limit:
        items = items[:limit]
    router = get_router() if model in ("frida", "assisted") else None
    preds: list[EvalPrediction] = []
    for it in items:
        if model == "heuristic":
            preds.append(predict_heuristic(it))
        elif model == "frida":
            assert router is not None
            p, _ = predict_frida(it, router)
            preds.append(p)
        elif model == "assisted":
            assert router is not None
            p_f, dec = predict_frida(it, router)
            pol = apply_policy(
                dec, intent_threshold=thr_i, clarify_threshold=thr_c, human_threshold=thr_h
            )
            if pol["action"] == "hint":
                preds.append(p_f)
            elif pol["action"] in ("clarify", "handoff", "fallback"):
                h = predict_heuristic(it)
                h.predicted_needs_human = p_f.predicted_needs_human or h.predicted_needs_human
                if pol["action"] == "handoff":
                    h.predicted_intent = "operator"
                    h.predicted_workflow = "handoff"
                    h.predicted_tool = None
                h.backend = f"assisted({p_f.backend})"
                h.latency_ms += p_f.latency_ms
                h.error = p_f.error
                preds.append(h)
        else:
            raise ValueError(f"unknown model {model}")
    metrics = compute_metrics(items, preds)
    return {"items": items, "preds": preds, "metrics": metrics}


def save(model: str, split: str, result: dict, extra: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(RESULTS_DIR / f"{model}_{split}.jsonl", "w", encoding="utf-8") as f:
        for p in result["preds"]:
            f.write(p.model_dump_json(ensure_ascii=False) + "\n")
    summary = {
        "model": model,
        "split": split,
        "dataset_version": DATASET_VERSION,
        "frida_pinned": f"ai-forever/FRIDA-Decisions@{FRIDA_VERSION_PIN}",
        "timestamp_utc": stamp,
        "seed": 42,
        "metrics": result["metrics"],
        **extra,
    }
    with open(RESULTS_DIR / f"{model}_{split}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return RESULTS_DIR / f"{model}_{split}_summary.json"


def tune(split: str) -> dict:
    items = load_items(split)
    router = get_router()
    decisions = []
    for it in items:
        state = build_frida_state(it.text)
        decisions.append((it, router.decide(state)))
    best = None
    grid = {
        "thr_i": [0.5, 0.65, 0.8, 0.9],
        "thr_h": [0.4, 0.5, 0.6, 0.7],
        "thr_c": [0.4, 0.5, 0.6, 0.7],
    }
    for ti, th, tc in itertools.product(grid["thr_i"], grid["thr_h"], grid["thr_c"]):
        preds = []
        for it, dec in decisions:
            pol = apply_policy(dec, intent_threshold=ti, clarify_threshold=tc, human_threshold=th)
            if pol["action"] == "hint":
                intent = dec.intent
            elif pol["action"] == "handoff":
                intent = "operator"
            else:
                h = predict_heuristic(it)
                intent = h.predicted_intent
            nh = dec.needs_human >= th
            nc = dec.needs_clarification >= tc or dec.confidence < ti
            preds.append(
                EvalPrediction(
                    id=it.id,
                    predicted_intent=intent,
                    predicted_needs_human=nh,
                    predicted_needs_clarification=nc,
                    predicted_workflow=INTENT_TO_WORKFLOW.get(intent, "clarify"),
                    predicted_tool=INTENT_TO_TOOL.get(intent),
                    confidence=dec.confidence,
                    latency_ms=dec.latency_ms,
                    backend=dec.backend,
                    error=dec.error,
                )
            )
        m = compute_metrics(items, preds)
        key = (m["intent_accuracy"], m["intent_macro_f1"])
        if best is None or key > best[0]:
            best = (key, {"thr_i": ti, "thr_h": th, "thr_c": tc, "metrics": m})
    assert best is not None
    return best[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="heuristic", choices=["heuristic", "frida", "assisted"])
    ap.add_argument("--split", default="all", choices=["all", "calibration", "validation", "test"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--thr-intent", type=float, default=DEFAULT_INTENT_THRESHOLD)
    ap.add_argument("--thr-human", type=float, default=DEFAULT_HUMAN_THRESHOLD)
    ap.add_argument("--thr-clarify", type=float, default=DEFAULT_CLARIFY_THRESHOLD)
    ap.add_argument("--tune", action="store_true")
    args = ap.parse_args()
    if args.tune:
        best = tune(args.split if args.split != "all" else "validation")
        print(json.dumps(best, ensure_ascii=False, indent=2))
        with open(RESULTS_DIR / "tune_validation.json", "w", encoding="utf-8") as f:
            json.dump(best, f, ensure_ascii=False, indent=2)
        return
    result = run(
        args.model, args.split, args.limit, args.thr_intent, args.thr_human, args.thr_clarify
    )
    path = save(
        args.model,
        args.split,
        result,
        {
            "thresholds": {
                "intent": args.thr_intent,
                "human": args.thr_human,
                "clarify": args.thr_clarify,
            }
        },
    )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"saved {path}")


if __name__ == "__main__":
    main()
