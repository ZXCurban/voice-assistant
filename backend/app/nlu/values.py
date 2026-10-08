"""Mapping of NLU slot values onto concrete values the backend understands."""

import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
_DOCTOR_MATCH_THRESHOLD = 0.78
_MIN_STEM = 4


def resolve_date(value: str, today: date) -> date | None:
    """Turn an NLU date value into a calendar day (clinic-local `today`).

    Weekday names always point to the next such day after today; `MM-DD`
    rolls over to next year when the day has already passed.
    """
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "day_after_tomorrow":
        return today + timedelta(days=2)
    kind, _, arg = value.partition(":")
    try:
        if kind == "weekday":
            ahead = (int(arg) - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
        if kind == "in_days":
            return today + timedelta(days=int(arg))
        if kind == "next_week":
            monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
            return monday_next + timedelta(days=int(arg))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.fullmatch(r"\d{2}-\d{2}", value):
            month, day = int(value[:2]), int(value[3:5])
            candidate = date(today.year, month, day)
            return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None
    return None


def to_local(starts_at: str, tz_name: str) -> datetime:
    """Parse an ISO instant and express it in the clinic time zone."""
    return datetime.fromisoformat(starts_at).astimezone(ZoneInfo(tz_name))


def format_dm(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}"


def format_hm(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def transliterate(text: str) -> str:
    """Rough Russian → Latin transliteration, enough for fuzzy name matching."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


def _surname_key(text: str) -> str:
    """Comparable stem of a surname: Latinised, Polish/Russian spellings folded.

    «Волкову», «Волков» and «Volkov» all end up as «volkov»: case
    endings and the Russian/Polish «-ский/-ski» difference are trimmed away.
    """
    latin = transliterate(text.casefold().strip())
    latin = latin.replace("w", "v").replace("ł", "l").replace("ó", "o")
    latin = unicodedata.normalize("NFKD", latin)
    latin = "".join(ch for ch in latin if not unicodedata.combining(ch))
    latin = re.sub(r"[^a-z]", "", latin)
    latin = latin.replace("cz", "ch").replace("sz", "sh").replace("j", "y")
    return re.sub(r"(?:[aeiouy]+|(?<=[a-z])[a-z]?(?=[aeiouy]+$))$", "", latin)


def match_doctors(surname: str, full_names: Sequence[str]) -> list[int]:
    """Indexes of `full_names` that match a spoken surname.

    The user speaks Cyrillic («Ковальскому»), the catalog may be Latin
    («Андрей Волков»): compare name tokens directly first, then by a
    case-insensitive surname stem, then fuzzily.
    """
    wanted = surname.strip().casefold()
    if not wanted:
        return []
    parts_of = [name.replace("-", " ").split() for name in full_names]
    exact = [
        index
        for index, parts in enumerate(parts_of)
        if wanted in (part.casefold() for part in parts)
    ]
    if exact:
        return exact
    wanted_key = _surname_key(wanted)
    matched: list[int] = []
    for index, parts in enumerate(parts_of):
        for part in parts:
            key = _surname_key(part)
            if min(len(key), len(wanted_key)) < _MIN_STEM and key != wanted_key:
                continue
            ratio = SequenceMatcher(None, wanted_key, key).ratio()
            if wanted_key == key or ratio >= _DOCTOR_MATCH_THRESHOLD:
                matched.append(index)
                break
    return matched


# Dialling codes of the cities the clinic network serves (NLU city vocabulary).
COUNTRY_CODES = {"moskva": "7", "sankt-peterburg": "7", "kazan": "7", "novosibirsk": "7"}
_LOCAL_NUMBER_LENGTH = 9


def normalize_phone(phone: str, city: str | None = None) -> str:
    """International form «+<code><number>» when the country can be told.

    «921 000 00 01» in Moskva → «+79210000001»; the Russian trunk prefix
    («8…», 11 digits) and bare 10-digit mobiles («9…») are recognised;
    «0048…» and «48…»/«+48…» stay recognised for other country codes via
    the generic branches below. A number that cannot be placed is returned
    compact, unchanged.
    """
    compact = re.sub(r"[\s\-()]", "", phone)
    digits = re.sub(r"\D", "", compact)
    if compact.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    code = COUNTRY_CODES.get(city or "")
    if code is None:
        return compact
    if code == "7":
        if len(digits) == 11 and digits.startswith("8"):
            return "+7" + digits[1:]
        if len(digits) == 10 and digits.startswith("9"):
            return "+7" + digits
    if len(digits) == _LOCAL_NUMBER_LENGTH:
        return f"+{code}{digits}"
    if digits.startswith(code) and len(digits) == len(code) + _LOCAL_NUMBER_LENGTH:
        return "+" + digits
    return compact


def phone_variants(phone: str, city: str | None = None) -> list[str]:
    """Spellings to try when looking a patient up by phone, best first."""
    compact = re.sub(r"[\s\-()]", "", phone)
    digits = re.sub(r"\D", "", compact)
    candidates = [normalize_phone(phone, city), compact, digits]
    if len(digits) > _LOCAL_NUMBER_LENGTH:
        candidates.insert(1, "+" + digits)
    seen: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen
