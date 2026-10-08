"""Export assistant dialogue logs to an NLU fine-tuning dataset (JSONL).

Input: turn records emitted by the ``assistant.dialogue`` logger
(see ``app/assistant/dialogue_log.py``) — one JSON object per assistant turn,
in chronological order. A full dialogue is all lines sharing
``conversation_id``.

Two input flavors are accepted (auto-detected per line):

- pure JSONL (file sink via ``ASSISTANT_DIALOG_LOG_PATH``);
- raw container output (``docker logs``): the stdlib prefix
  (``... INFO [assistant.dialogue] ``) is stripped, unrelated lines
  (uvicorn access logs, ...) are ignored.

Output: one dataset row per user turn::

    {"text": ..., "intent": ..., "confidence": ..., "slots": {...},
     "assistant_text": ..., "event": ..., "conversation_id": ...,
     "turn_index": ..., "ts": ...}

Error turns (``error`` set) are dropped by default. Secrets are already
redacted at logging time; names/phones are kept as NLU slots (synthetic
data only).

Usage:
    python scripts/export_dialogue_logs.py --input var/assistant_dialogues.jsonl
    docker logs voice-assistant-api-1 2>&1 | python scripts/export_dialogue_logs.py --stats
    python scripts/export_dialogue_logs.py --input logs.jsonl --output dataset.jsonl --stats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.assistant.dialogue_log import (  # noqa: E402
    DialogueTurnLog,
    group_dialogues,
    parse_log_line,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="-",
        help="Log file: pure JSONL or raw `docker logs` output (default: stdin).",
    )
    parser.add_argument("--output", default="-", help="Dataset JSONL path (default: stdout).")
    parser.add_argument(
        "--keep-errors",
        action="store_true",
        help="Keep turns with error markers (default: drop them).",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="Drop rows below this NLU confidence (default: 0.0).",
    )
    parser.add_argument("--stats", action="store_true", help="Print counters to stderr.")
    return parser.parse_args(argv)


def _read_records(source: str) -> tuple[list[DialogueTurnLog], int, int]:
    """Read turn records; return (records, corrupt, ignored_unrelated)."""
    if source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    records: list[DialogueTurnLog] = []
    corrupt = 0
    ignored = 0
    for line in text.splitlines():
        try:
            record = parse_log_line(line)
        except ValueError:
            corrupt += 1
            continue
        if record is None:
            if line.strip():
                ignored += 1
            continue
        records.append(record)
    return records, corrupt, ignored


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    records, corrupt, ignored = _read_records(args.input)
    dropped_errors = 0
    dropped_confidence = 0
    rows: list[dict[str, object]] = []
    for turns in group_dialogues(records).values():
        for index, turn in enumerate(turns):
            if turn.error is not None and not args.keep_errors:
                dropped_errors += 1
                continue
            row = turn.to_dataset_item(index)
            if float(row["confidence"]) < args.min_confidence:
                dropped_confidence += 1
                continue
            rows.append(row)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if args.output == "-":
        sys.stdout.write("\n".join(lines))
        if lines:
            sys.stdout.write("\n")
    else:
        Path(args.output).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
    if args.stats:
        print(
            f"turns={len(records)} conversations={len(group_dialogues(records))} "
            f"rows={len(rows)} dropped_errors={dropped_errors} "
            f"dropped_confidence={dropped_confidence} corrupt={corrupt} "
            f"ignored_unrelated={ignored}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
