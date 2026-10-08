"""NLU contract: fixed intent/slot vocabularies and the shared validators.

Mirrors `ml-training/src/nlu_contract.py`, `slot_validator.py` and
`model_input.py`. The models were trained against exactly these rules, so
changing them here without retraining silently degrades quality.
"""

import re
from collections.abc import Mapping
from typing import Final

INTENTS: Final[tuple[str, ...]] = (
    "greeting",
    "confirm",
    "reject",
    "select_option",
    "book_appointment",
    "find_nearest_slots",
    "find_slots",
    "find_doctors",
    "find_clinics",
    "cancel_appointment",
    "reschedule_appointment",
    "get_appointments",
    "get_appointment",
    "health_concern",
    "unknown_request",
)

# `unknown_request` doubles as an «informative turn»: the meaning of
# «кардиолог», «завтра после двух» or a phone number is carried by slots.
INFORMATIVE_INTENT: Final = "unknown_request"

SLOT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "specialty",
        "date",
        "time",
        "time_after",
        "selection_time",
        "selection",
        "city",
        "patient_mode",
        "phone",
        "full_name",
        "doctor",
        "branch",
    }
)

SPECIALTIES: Final[frozenset[str]] = frozenset(
    {
        "cardiology",
        "dermatology",
        "neurology",
        "ophthalmology",
        "pediatrics",
        "dentistry",
        "surgery",
        "otolaryngology",
        "critical_care",
        "therapist",
    }
)
CITIES: Final[frozenset[str]] = frozenset({"moskva", "sankt-peterburg", "kazan", "novosibirsk"})
PATIENT_MODES: Final[frozenset[str]] = frozenset({"new", "registered"})
SELECTIONS: Final[frozenset[str]] = frozenset({"earliest", "latest", "1", "2", "3", "4", "5", "6"})

TIME_RE: Final = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE: Final = re.compile(
    r"^(today|tomorrow|day_after_tomorrow"
    r"|weekday:[0-6]"
    r"|in_days:\d{1,3}"
    r"|next_week:[0-6]"
    r"|\d{2}-\d{2}"
    r"|\d{4}-\d{2}-\d{2})$"
)
PHONE_RE: Final = re.compile(r"^\+?\d[\d\s\-()]{7,17}\d$")

CONTEXT_CHARS: Final = 200
SEP: Final = " [SEP] "


def build_model_input(
    text: str, last_response: str = "", context_chars: int = CONTEXT_CHARS
) -> str:
    """Model input: «<previous assistant reply, first 200 chars> [SEP] <user text>».

    The first turn (no previous reply) is just the user text.
    """
    text = (text or "").strip()
    last_response = (last_response or "").strip()
    if not last_response:
        return text
    ctx = last_response[:context_chars].strip()
    if not ctx:
        return text
    return f"{ctx}{SEP}{text}"


def clean_phone(value: str) -> str | None:
    """Return the phone in compact form or None if it does not look like one."""
    raw = value.strip()
    if not PHONE_RE.match(raw):
        return None
    compact = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    digits = re.sub(r"\D", "", compact)
    if not 9 <= len(digits) <= 15:
        return None
    return compact


def _clean_time(value: str) -> str | None:
    return value if TIME_RE.fullmatch(value) else None


def _clean_date(value: str) -> str | None:
    if not DATE_RE.fullmatch(value):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        month, day = int(value[5:7]), int(value[8:10])
    elif re.fullmatch(r"\d{2}-\d{2}", value):
        month, day = int(value[:2]), int(value[3:5])
    else:
        return value
    return value if 1 <= month <= 12 and 1 <= day <= 31 else None


def clean_full_name(value: str) -> str | None:
    """Full name = at least two words, each starting with an uppercase letter."""
    parts = value.strip().split()
    if len(parts) < 2 or not all(p[:1].isupper() for p in parts):
        return None
    return " ".join(parts)


def validate_slot(name: str, value: str) -> str | None:
    """Return the cleaned value, or None if the slot or its value is invalid."""
    if name not in SLOT_NAMES:
        return None
    v = str(value).strip()
    if not v:
        return None
    closed: dict[str, frozenset[str]] = {
        "specialty": SPECIALTIES,
        "city": CITIES,
        "patient_mode": PATIENT_MODES,
        "selection": SELECTIONS,
    }
    if name in closed:
        return v if v in closed[name] else None
    if name in ("time", "time_after", "selection_time"):
        return _clean_time(v)
    if name == "date":
        return _clean_date(v)
    if name == "phone":
        return clean_phone(v)
    if name == "full_name":
        return clean_full_name(v)
    return v  # doctor / branch: free-form surname or branch, must be non-empty


def clean_slots(slots: Mapping[str, str]) -> dict[str, str]:
    """Drop unknown slot names and invalid values. Never invents slots."""
    cleaned: dict[str, str] = {}
    for name, value in slots.items():
        result = validate_slot(name, value)
        if result is not None:
            cleaned[name] = result
    return cleaned


def parse_slots_linear(text: str) -> dict[str, str]:
    """Parse the seq2seq output «k=v; k=v» / «none» into a raw dict."""
    text = (text or "").strip()
    if not text or text.lower() == "none":
        return {}
    out: dict[str, str] = {}
    for chunk in text.split(";"):
        key, sep, value = chunk.strip().partition("=")
        key, value = key.strip(), value.strip()
        if sep and key and value:
            out[key] = value
    return out
