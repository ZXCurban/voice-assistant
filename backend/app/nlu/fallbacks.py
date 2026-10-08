"""Deterministic helpers used next to the models.

The dialogue manager knows which answer it is waiting for (a phone number,
a name, a city, …). In those states a precise rule is more reliable than a
seq2seq model, so these extractors back up the NLU slots; they never
replace a value the NLU already produced.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from app.nlu.contract import clean_full_name, clean_phone

_PHONE_CANDIDATE = re.compile(r"(?<![\d+])\+?\d[\d\s\-()]{7,19}\d")
_NAME_PREFIX = re.compile(
    r"^(?:меня\s+зовут|мо[её]\s+имя|я|это|имя)\s+", re.IGNORECASE | re.UNICODE
)
_CAPITALIZED_NAME = re.compile(r"[A-ZА-ЯЁ][a-zа-яё]+(?:[ \t]+[A-ZА-ЯЁ][a-zа-яё]+){1,2}", re.UNICODE)
_PLAIN_WORDS = re.compile(r"^[A-Za-zА-Яа-яЁё\-]+(?:\s+[A-Za-zА-Яа-яЁё\-]+){1,2}$", re.UNICODE)

_SPECIALTY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cardiology", re.compile(r"кардиолог|сердц|сердеч")),
    ("dermatology", re.compile(r"дерматолог|кожник|кожн")),
    ("neurology", re.compile(r"невролог|невропатолог")),
    ("pediatrics", re.compile(r"педиатр|детск")),
    ("dentistry", re.compile(r"стоматолог|зубн|дантист|зубник")),
    ("surgery", re.compile(r"хирург")),
    ("otolaryngology", re.compile(r"\bлор(?:а|у|е|ом)?\b|отоларинг")),
    ("ophthalmology", re.compile(r"окулист|офтальмолог|глазн|глазник")),
    ("therapist", re.compile(r"терапевт|участков")),
    ("critical_care", re.compile(r"реаниматолог")),
)

_NEW_PATIENT = re.compile(
    r"впервые|первый\s+раз|первичн|нов(?:ый|ая|енький|енькая)|не\s+был|не\s+была|"
    r"не\s+обращал|не\s+записывал|ещ[её]\s+не|никогда"
)
_REGISTERED_PATIENT = re.compile(r"\bбыл[аи]?\b|\bуже\b|регистр|карточк|постоянн|ранее|раньше")

_YES_WORDS = frozenset(
    {
        "да",
        "ага",
        "угу",
        "конечно",
        "подтверждаю",
        "верно",
        "давайте",
        "давай",
        "хорошо",
        "ок",
        "окей",
        "согласен",
        "согласна",
        "пожалуйста",
        "записывайте",
        "запишите",
        "запиши",
        "записывай",
        "оформляйте",
        "оформляй",
        # Colloquial go-aheads from real dialogues («пойдет», «сойдет»):
        "пойдет",
        "сойдет",
        "нормально",
        "норм",
        "ладно",
        "годится",
        "подходит",
        "подойдет",
        "устроит",
        "устраивает",
        "отлично",
        "супер",
        "класс",
        "идет",
    }
)
_NO_WORDS = frozenset({"нет", "неа", "не", "отмена", "отменить", "стоп", "хватит", "отбой"})
_NO_PHRASES = ("не надо", "не нужно", "не хочу", "не стоит")
# «отменяйте» answering «Отменить запись …?» means «yes, cancel it»; the intent
# model reads the bare verb as a rejection.
_CANCEL_GO_AHEAD = re.compile(r"^(?:да[, ]+)?отмен(?:ите|яйте|яй|и|ить)\b")
_MIN_PHONE_DIGITS = 7
_THANKS = re.compile(r"^(?:большое\s+)?(?:спасибо|благодарю|спс|благодарствую)\b")
_BYE = re.compile(
    r"^(?:пока|до\s+свидания|до\s+встречи|всего\s+(?:доброго|хорошего)|прощайте|бывайте)\b"
)
_HELP = re.compile(
    r"что\s+(?:ты|вы)\s+(?:умеешь|можете|умеете)|чем\s+(?:ты|вы)\s+(?:можешь|можете)\s+помочь|помощь|help"
)
_ANOTHER_DATE = re.compile(r"друг(?:ую|ой|ое)\s+(?:дат|день|числ)|на\s+друг")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower().replace("ё", "е")


def extract_phone(text: str) -> str | None:
    """First phone-looking digit sequence of the text, in compact form."""
    for match in _PHONE_CANDIDATE.finditer(text):
        cleaned = clean_phone(match.group(0))
        if cleaned is not None:
            return cleaned
    return None


def extract_full_name(text: str) -> str | None:
    """Name from a short answer to «как к вам обращаться»: two+ words."""
    stripped = _NAME_PREFIX.sub("", text.strip().rstrip(".!"))
    match = _CAPITALIZED_NAME.search(stripped)
    if match is not None:
        return clean_full_name(match.group(0))
    if _PLAIN_WORDS.match(stripped):
        return clean_full_name(" ".join(word.capitalize() for word in stripped.split()))
    return None


def phone_in_text(phone: str, text: str) -> bool:
    """True if the digits of `phone` and of `text` contain one another.

    Two-way: the model may add a country code the user did not say.
    """
    wanted = re.sub(r"\D", "", phone)
    said = re.sub(r"\D", "", text)
    if len(wanted) < _MIN_PHONE_DIGITS or len(said) < _MIN_PHONE_DIGITS:
        return False
    return wanted in said or said in wanted


def name_in_text(name: str, text: str) -> bool:
    """True if every word of `name` was really said (the model may hallucinate)."""
    said = set(re.findall(r"[a-zа-я]+", _normalize(text)))
    words = re.findall(r"[a-zа-я]+", _normalize(name))
    return bool(words) and all(word in said for word in words)


def detect_specialty(text: str) -> str | None:
    """Specialty named in free text (closed contract vocabulary), else None."""
    normalized = _normalize(text)
    for specialty, pattern in _SPECIALTY_PATTERNS:
        if pattern.search(normalized):
            return specialty
    return None


_FUZZY_ROOTS: tuple[tuple[str, str], ...] = (
    ("cardiology", "кардиолог"),
    ("dermatology", "дерматолог"),
    ("neurology", "невролог"),
    ("pediatrics", "педиатр"),
    ("dentistry", "стоматолог"),
    ("surgery", "хирург"),
    ("otolaryngology", "отоларинголог"),
    ("ophthalmology", "офтальмолог"),
    ("ophthalmology", "окулист"),
    ("therapist", "терапевт"),
    ("critical_care", "реаниматолог"),
)
_FUZZY_MIN_WORD = 6
_FUZZY_RATIO = 0.82


def detect_specialty_fuzzy(text: str) -> str | None:
    """Specialty named with a typo («кордиологу», «дерматолог а»), else None."""
    best: tuple[float, str] | None = None
    for word in re.findall(r"[а-я]+", _normalize(text)):
        if len(word) < _FUZZY_MIN_WORD:
            continue
        for specialty, root in _FUZZY_ROOTS:
            ratio = SequenceMatcher(None, word[: len(root) + 1], root).ratio()
            if ratio >= _FUZZY_RATIO and (best is None or ratio > best[0]):
                best = (ratio, specialty)
    return None if best is None else best[1]


_RELATIVE_DAYS = (
    ("day_after_tomorrow", "послезавтра"),
    ("tomorrow", "завтра"),
    ("today", "сегодня"),
)
_RELATIVE_RATIO = 0.8


def detect_relative_date(text: str) -> str | None:
    """«сегодня» / «завтра» / «послезавтра», also with a typo («завтро»)."""
    words = re.findall(r"[а-я]+", re.sub(r"после\s+за", "послеза", _normalize(text)))
    for value, root in _RELATIVE_DAYS:
        if root in words:
            return value
    best: tuple[float, str] | None = None
    for word in words:
        for value, root in _RELATIVE_DAYS:
            ratio = SequenceMatcher(None, word, root).ratio()
            if ratio >= _RELATIVE_RATIO and (best is None or ratio > best[0]):
                best = (ratio, value)
    return None if best is None else best[1]


def specialty_mentioned(specialty: str, text: str) -> bool:
    """True if the text names this very specialty (exactly, no typos)."""
    normalized = _normalize(text)
    return any(name == specialty and rx.search(normalized) for name, rx in _SPECIALTY_PATTERNS)


def detect_patient_mode(text: str) -> str | None:
    """«впервые» → new, «уже был» → registered; a bare «нет» means new."""
    normalized = _normalize(text)
    if _NEW_PATIENT.search(normalized) or normalized.strip(" .!") == "нет":
        return "new"
    if _REGISTERED_PATIENT.search(normalized):
        return "registered"
    return None


def detect_yes_no(text: str) -> str | None:
    """«yes» / «no» for a short answer to a yes-no question, else None."""
    normalized = _normalize(text).strip(" .!?,")
    words = re.findall(r"[a-zа-я]+", normalized)
    if not words or len(words) > 4:
        return None
    if any(phrase in normalized for phrase in _NO_PHRASES) or words[0] in _NO_WORDS:
        return "no"
    if words[0] in _YES_WORDS:
        return "yes"
    if "да" in words and not any(word in ("не", "нет", "ни") for word in words):
        # «дура. да», «ну да, записывай»: grumpy but unambiguous agreement.
        return "yes"
    return None


def is_cancel_go_ahead(text: str) -> bool:
    """«отменяйте» / «да, отмените» as the answer to a cancellation question."""
    return _CANCEL_GO_AHEAD.match(_normalize(text).strip(" .!")) is not None


def wants_another_date(text: str) -> bool:
    """«давайте другую дату» after «no free time on that date»."""
    return _ANOTHER_DATE.search(_normalize(text)) is not None


def detect_smalltalk(text: str) -> str | None:
    """«thanks» / «bye» / «help» for the polite phrases outside any task."""
    normalized = _normalize(text).strip(" .!?,")
    if _THANKS.match(normalized):
        return "thanks"
    if _BYE.match(normalized):
        return "bye"
    if _HELP.search(normalized):
        return "help"
    return None
