"""Reproducible evaluation CLI (heuristic baseline only).

Usage:
  python scripts/run_eval.py --model heuristic --split all

Models:
  heuristic — keyword proxy of the current LLM-only tool-loop routing.
    NOT the real Qwen3 model: it approximates intent/tool selection so the
    harness, metrics, and A/B plumbing are testable without a GPU LLM
    server. A live-LLM run is tracked as follow-up (needs LLM_BASE_URL).

Results: backend/tests/eval/results/<model>_<split>.jsonl + _summary.json
with seed/version/timestamp environment info.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.eval.metrics import compute_metrics  # noqa: E402
from app.eval.schemas import EvalItem, EvalPrediction  # noqa: E402

DATA_DIR = ROOT / "backend" / "tests" / "eval" / "data"
RESULTS_DIR = ROOT / "backend" / "tests" / "eval" / "results"
DATASET_VERSIONS = {"v1": "clinic_intents_v1", "v2": "clinic_intents_v2"}

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


def load_items(split: str, dataset: str = "v1") -> list[EvalItem]:
    prefix = DATASET_VERSIONS[dataset]
    if split == "all":
        path = DATA_DIR / f"{prefix}.jsonl"
    else:
        suffix = "" if dataset == "v1" else "_v2"
        path = DATA_DIR / f"split_{split}{suffix}.jsonl"
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


def run(
    model: str,
    split: str,
    limit: int | None,
    dataset: str = "v1",
) -> dict:
    items = load_items(split, dataset)
    if limit:
        items = items[:limit]
    preds: list[EvalPrediction] = []
    for it in items:
        if model == "heuristic":
            preds.append(predict_heuristic(it))
        else:
            raise ValueError(f"unknown model {model}")
    metrics = compute_metrics(items, preds)
    return {"items": items, "preds": preds, "metrics": metrics}


def save(model: str, split: str, result: dict, extra: dict, dataset: str = "v1") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tag = f"{model}_{split}" if dataset == "v1" else f"{model}_{split}_{dataset}"
    with open(RESULTS_DIR / f"{tag}.jsonl", "w", encoding="utf-8") as f:
        for p in result["preds"]:
            f.write(p.model_dump_json(ensure_ascii=False) + "\n")
    summary = {
        "model": model,
        "split": split,
        "dataset_version": DATASET_VERSIONS[dataset],
        "timestamp_utc": stamp,
        "seed": 42,
        "metrics": result["metrics"],
        **extra,
    }
    with open(RESULTS_DIR / f"{tag}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return RESULTS_DIR / f"{tag}_summary.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="heuristic", choices=["heuristic"])
    ap.add_argument("--split", default="all", choices=["all", "calibration", "validation", "test"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dataset", default="v1", choices=["v1", "v2"])
    ap.add_argument("--seed-note", default="", help="free-form run note stored in summary")
    args = ap.parse_args()
    result = run(
        args.model,
        args.split,
        args.limit,
        args.dataset,
    )
    path = save(
        args.model,
        args.split,
        result,
        {"note": args.seed_note},
        args.dataset,
    )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"saved {path}")


if __name__ == "__main__":
    main()
