"""Deterministic normalization from NLU candidates into backend request values."""

from __future__ import annotations

import re
from datetime import date, time, timedelta

from app.assistant.nlu import NluParse

INTENT_MAP = {
    "find_slots": "find_nearest_slots",
    "book_appointment": "find_nearest_slots",
    "get_appointments": "get_appointments",
    "get_appointment": "get_appointments",
    "cancel_appointment": "cancel_appointment",
    "reschedule_appointment": "reschedule_appointment",
    "find_clinics": "find_clinics",
    "find_doctors": "find_doctors",
}


def normalize_date(value: str, today: date | None = None) -> date | None:
    """Resolve ISO, day-month, today and tomorrow to a calendar date."""
    base = today or date.today()
    if value == "today":
        return base
    if value == "tomorrow":
        return base + timedelta(days=1)
    if value == "day_after_tomorrow":
        return base + timedelta(days=2)
    if value.startswith("weekday:"):
        try:
            weekday = int(value.partition(":")[2])
        except ValueError:
            return None
        if not 0 <= weekday <= 6:
            return None
        days_ahead = (weekday - base.weekday()) % 7 or 7
        return base + timedelta(days=days_ahead)
    if re.fullmatch(r"\d{2}-\d{2}", value):
        try:
            candidate = date.fromisoformat(f"{base.year}-{value}")
        except ValueError:
            return None
        return candidate if candidate >= base else date.fromisoformat(f"{base.year + 1}-{value}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def normalize_time(value: str) -> time | None:
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def normalize(parse: NluParse, *, clinic_id: int | None) -> dict[str, object]:
    """Build a typed intent payload; retain only sufficiently confident slots."""
    slots = {name: item.value for name, item in parse.slots.items() if item.confidence >= 0.75}
    action = INTENT_MAP.get(parse.intent)
    out: dict[str, object] = {"intent": action or "unknown_request", "clinic_id": clinic_id}
    if "specialty" in slots:
        out["specialty_name"] = str(slots["specialty"])
    if "doctor" in slots:
        out["doctor_name"] = str(slots["doctor"])
    if "date" in slots:
        resolved_date = normalize_date(str(slots["date"]))
        if resolved_date is None:
            out["date_invalid"] = True
        else:
            out["date"] = resolved_date
    if "time" in slots:
        parsed_time = normalize_time(str(slots["time"]))
        if parsed_time is not None:
            out["time_at"] = parsed_time
    if "time_after" in slots:
        parsed_time = normalize_time(str(slots["time_after"]))
        if parsed_time is not None:
            out["time_after"] = parsed_time
    if parse.intent == "find_clinics" and "branch" in slots:
        out["city"] = str(slots["branch"])
    elif "city" in slots:
        out["city"] = str(slots["city"])
    return out
