"""Validate the clinic intent dataset (also covered by pytest)."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.eval.intents import ASSISTANT_INTENTS  # noqa: E402
from app.eval.schemas import EvalItem  # noqa: E402

DATA = ROOT / "backend" / "tests" / "eval" / "data"


def main() -> int:
    rows = [EvalItem(**json.loads(line)) for line in (DATA / "clinic_intents_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert 300 <= len(rows) <= 500, len(rows)
    assert len({r.id for r in rows}) == len(rows), "dup ids"
    assert all(r.text.strip() for r in rows), "empty text"
    assert len({r.text.strip() for r in rows}) == len(rows), "dup texts"
    bad = [r.id for r in rows if r.expected_intent not in ASSISTANT_INTENTS]
    assert not bad, bad
    print(f"OK: {len(rows)} rows, categories={dict(Counter(r.category for r in rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
