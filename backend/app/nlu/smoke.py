"""Smoke check of the real NLU weights, no database needed.

    python -m app.nlu.smoke --model-dir ../ml-training/artifacts
    python -m app.nlu.smoke --model-dir … "хочу к кардиологу завтра" "да"

Without utterances a built-in set runs. Prints the intent, the confidence
and the slots per utterance; exit code 1 if the weights cannot be loaded or
an expectation of the built-in set is not met.
"""

import argparse
import sys
from pathlib import Path

from app.nlu.engine import NluUnavailableError, TorchNluEngine

# (utterance, assistant's previous reply, expected intent, expected slots)
BUILTIN: tuple[tuple[str, str, str, dict[str, str]], ...] = (
    ("Здравствуйте", "", "greeting", {}),
    ("хочу записаться к кардиологу", "", "book_appointment", {"specialty": "cardiology"}),
    ("завтра", "На какую дату подобрать время?", "unknown_request", {"date": "tomorrow"}),
    (
        "в Варшаве",
        "В каком городе или филиале вам удобнее?",
        "unknown_request",
        {"city": "warszawa"},
    ),
    (
        "второй",
        "Доступное время: 1 — 09.10 в 09:00; 2 — 09.10 в 09:30; Какой вариант выбрать?",
        "select_option",
        {"selection": "2"},
    ),
    (
        "да",
        "Записать вас на 09.10 в 09:30? Скажите «да» или «нет».",
        "confirm",
        {},
    ),
    ("какие у меня записи", "", "get_appointments", {}),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--model-dir", default="models/nlu", help="ml-training artifacts/ directory"
    )
    parser.add_argument("utterances", nargs="*")
    args = parser.parse_args(argv)
    try:
        engine = TorchNluEngine(Path(args.model_dir))
    except NluUnavailableError as exc:
        print(f"NLU unavailable: {exc}", file=sys.stderr)
        return 1
    failed = 0
    if args.utterances:
        for text in args.utterances:
            parse = engine.parse(text)
            print(f"{text!r}: {parse.intent} ({parse.confidence:.3f}) {parse.slots}")
        return 0
    for text, context, intent, slots in BUILTIN:
        parse = engine.parse(text, context)
        ok = parse.intent == intent and all(parse.slots.get(k) == v for k, v in slots.items())
        failed += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {text!r}: {parse.intent} ({parse.confidence:.3f}) {parse.slots}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
