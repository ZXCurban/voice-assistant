"""Deterministic Russian NLU baseline with a stable fine-tuning contract.

The parser identifies intent and copies candidate entities. It does not
select backend operations or produce user-facing text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.services.geo import canonical_city


class SlotCandidate(BaseModel):
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class NluParse(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    slots: dict[str, SlotCandidate] = Field(default_factory=dict)


@dataclass(frozen=True)
class _IntentRule:
    intent: str
    patterns: tuple[str, ...]


_RULES = (
    _IntentRule(
        "greeting", (r"^(?:привет|здравствуй(?:те)?|добрый день|доброе утро|добрый вечер)[!. ]*$",)
    ),
    _IntentRule("cancel_appointment", (r"\bотмен\w*\b", r"\bне приду\b")),
    _IntentRule("reschedule_appointment", (r"\bперенес\w*\b", r"\bперестав\w*\b")),
    _IntentRule("get_appointments", (r"\bмои запис\w*\b", r"\bмои при[её]м\w*\b")),
    _IntentRule("get_appointment", (r"\bстатус\w* запис\w*\b", r"\bкогда у меня при[её]м\b")),
    _IntentRule("find_clinics", (r"\bклиник\w*\b", r"\bадрес\w*\b")),
    _IntentRule(
        "health_concern",
        (
            r"\b(?:болит|бол\w*|боит\w*|ноет|ломит)\b.*\b(?:голов\w*|ух\w*|ног\w*|живот\w*|спин\w*|горл\w*)",
            r"\b(?:голов\w*|ух\w*|ног\w*|живот\w*|спин\w*|горл\w*)\b.*\b(?:болит|бол\w*|боит\w*|ноет|ломит)\b",
        ),
    ),
    _IntentRule(
        "find_nearest_slots",
        (
            r"\bближайш\w*\s+(?:свободн\w*\s+)?(?:окн\w*|врем\w*|слот\w*)",
            r"\b(?:найд\w*|подбер\w*|покаж\w*)\b.*\bближайш\w*\b",
            r"\b(?:найд\w*|подбер\w*|покаж\w*)\b.*\b(?:свободн\w*\s+)?(?:окн\w*|врем\w*|слот\w*)",
        ),
    ),
    _IntentRule(
        "find_slots",
        (
            r"\bрасписан\w*\b",
            r"\bсвободн\w*\s+окн\w*\b",
            r"\b(?:найд\w*|подбер\w*|покаж\w*|есть)\b.*\b(?:свободн\w*\s+)?(?:окн\w*|врем\w*|слот\w*)",
        ),
    ),
    _IntentRule(
        "find_doctors",
        (
            r"\bкакие врач\w*\b",
            r"\bсписок врач\w*\b",
            r"\bнайд[иу]\b",
            r"\bищу\b",
            r"\bнужен\s+(?:мне\s+)?(?:врач|доктор)\b",
            r"\bмне нужен\b",
        ),
    ),
    _IntentRule(
        "book_appointment",
        (r"\bзапис\w*\b", r"\bзапиш\w*\b", r"\bпри[её]м\w* к\b", r"\bпопасть к\b"),
    ),
)

_SPECIALTIES = {
    "cardiology": ("кардиолог", "кардиологии", "кардиологу", "кардиолога", "сердце"),
    "dermatology": ("дерматолог", "дерматологии", "дерматологу", "дерматолога", "кож"),
    "neurology": ("невролог", "неврологии", "неврологу", "невролога"),
    "ophthalmology": ("офтальмолог", "офтальмологу", "офтальмолога", "окулист", "офтальмологии"),
    "pediatrics": ("педиатр", "педиатру", "педиатра", "детский врач"),
    "dentistry": ("стоматолог", "стоматологу", "стоматолога", "зубной врач", "стоматологии"),
    "surgery": ("хирург", "хирургу", "хирурга", "хирургия"),
    "otolaryngology": ("лор", "отоларинголог", "отоларингологу", "отоларинголога"),
    "critical_care": ("реаниматолог", "реаниматологу", "реаниматолога"),
    "therapist": ("терапевт", "терапевту", "терапевта", "терапии"),
}
_HOUR_WORDS = {
    "одного": 1,
    "один": 1,
    "двух": 2,
    "два": 2,
    "трех": 3,
    "трёх": 3,
    "три": 3,
    "четырех": 4,
    "четырёх": 4,
    "пяти": 5,
    "шести": 6,
    "семи": 7,
    "восьми": 8,
    "девяти": 9,
    "десяти": 10,
    "одиннадцати": 11,
    "двенадцати": 12,
}
_WEEKDAYS_RU = {
    "понедель": 0,
    "вторник": 1,
    "сред": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскрес": 6,
}


def parse_utterance(text: str) -> NluParse:
    """Parse intent and high precision candidates; uncertain text stays unknown."""
    normalized = " ".join(text.casefold().split())
    patient_mode: str | None = None
    if re.fullmatch(
        r"(?:да|давай|подтверждаю|подтвердить|согласен|согласна|ок|хорошо)[!. ]*", normalized
    ):
        return NluParse(intent="confirm", confidence=0.99)
    if re.fullmatch(r"(?:нет|не надо|отмена|отменить|не подтверждаю|стоп)[!. ]*", normalized):
        return NluParse(intent="reject", confidence=0.99)
    if re.fullmatch(r"(?:самый ранний|пораньше|ранний)[!. ]*", normalized):
        return NluParse(
            intent="select_option",
            confidence=0.96,
            slots={"selection": SlotCandidate(value="earliest", confidence=0.99)},
        )
    if re.search(
        r"\b(?:я\s+)?(?:впервые|новый пациент|новая пациентка|раньше не был\w*|"
        r"раньше не был\w*|не обращал\w*ся|нет карты)\b",
        normalized,
    ):
        patient_mode = "new"
    if re.search(
        r"\b(?:уже был\w*|уже обращал\w*ся|я ваш пациент|я пациент|"
        r"зарегистрирован\w*|есть карта|есть карточка)\b",
        normalized,
    ):
        patient_mode = "registered"
    if re.fullmatch(r"(?:последний|самый поздний|позже всех)[!. ]*", normalized):
        return NluParse(
            intent="select_option",
            confidence=0.96,
            slots={"selection": SlotCandidate(value="latest", confidence=0.99)},
        )
    clock_selection = re.fullmatch(r"(\d{1,2})[:.](\d{2})[!. ]*", normalized)
    if clock_selection:
        hour, minute = (int(part) for part in clock_selection.groups())
        if hour < 24 and minute < 60:
            return NluParse(
                intent="select_option",
                confidence=0.96,
                slots={
                    "selection_time": SlotCandidate(
                        value=f"{hour:02d}:{minute:02d}", confidence=0.99
                    )
                },
            )
    ordinal = re.fullmatch(
        r"(?:первый|первая|первое|1|второй|вторая|второе|2|третий|третья|3|"
        r"четвертый|четвертая|четв[её]ртый|4|пятый|пятая|5|шестой|6)"
        r"(?:\s+(?:вариант|слот))?[!. ]*",
        normalized,
    )
    if ordinal:
        ordinal_map = {
            "первый": "1",
            "первая": "1",
            "первое": "1",
            "1": "1",
            "второй": "2",
            "вторая": "2",
            "второе": "2",
            "2": "2",
            "третий": "3",
            "третья": "3",
            "3": "3",
            "четвертый": "4",
            "четвертая": "4",
            "четвёртый": "4",
            "4": "4",
            "пятый": "5",
            "пятая": "5",
            "5": "5",
            "шестой": "6",
            "6": "6",
        }
        word = normalized.split()[0].rstrip("!.")
        return NluParse(
            intent="select_option",
            confidence=0.96,
            slots={"selection": SlotCandidate(value=ordinal_map[word], confidence=0.99)},
        )
    intent = "unknown_request"
    confidence = 0.25 if normalized else 0.0
    negated_action = re.search(
        r"\bне\s+(?:хочу\s+)?(?:запис\w*|отмен\w*|перенес\w*|перестав\w*)",
        normalized,
    )
    if negated_action is None:
        for rule in _RULES:
            if any(re.search(pattern, normalized) for pattern in rule.patterns):
                intent, confidence = rule.intent, 0.88
                break

    slots: dict[str, SlotCandidate] = {}
    if patient_mode is not None:
        slots["patient_mode"] = SlotCandidate(value=patient_mode, confidence=0.96)
    matches = [
        (normalized.rfind(variant), canonical)
        for canonical, variants in _SPECIALTIES.items()
        for variant in variants
        if variant in normalized
    ]
    if matches:
        matches.sort()
        if len({canonical for _, canonical in matches}) == 1 or re.search(
            r"\b(?:а|лучше|вернее|точнее)\b", normalized
        ):
            slots["specialty"] = SlotCandidate(value=matches[-1][1], confidence=0.94)
    if re.search(r"\bпослезавтра\b", normalized):
        slots["date"] = SlotCandidate(value="day_after_tomorrow", confidence=0.98)
    elif re.search(r"\bзавтра\b", normalized):
        slots["date"] = SlotCandidate(value="tomorrow", confidence=0.98)
    elif re.search(r"\bсегодня\b", normalized):
        slots["date"] = SlotCandidate(value="today", confidence=0.98)
    else:
        weekday = next(
            (
                day_index
                for prefix, day_index in _WEEKDAYS_RU.items()
                if re.search(rf"\b{prefix}\w*\b", normalized)
            ),
            None,
        )
        if weekday is not None:
            slots["date"] = SlotCandidate(value=f"weekday:{weekday}", confidence=0.9)
        else:
            date_match = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b", normalized)
            if date_match:
                day, month, year = date_match.groups()
                value = (
                    f"{year}-{int(month):02d}-{int(day):02d}"
                    if year is not None
                    else f"{int(month):02d}-{int(day):02d}"
                )
                slots["date"] = SlotCandidate(value=value, confidence=0.9)
            else:
                months = {
                    "января": 1,
                    "январь": 1,
                    "февраля": 2,
                    "февраль": 2,
                    "марта": 3,
                    "март": 3,
                    "апреля": 4,
                    "апрель": 4,
                    "мая": 5,
                    "июня": 6,
                    "июнь": 6,
                    "июля": 7,
                    "июль": 7,
                    "августа": 8,
                    "август": 8,
                    "сентября": 9,
                    "сентябрь": 9,
                    "октября": 10,
                    "октябрь": 10,
                    "ноября": 11,
                    "ноябрь": 11,
                    "декабря": 12,
                    "декабрь": 12,
                }
                month_pattern = "|".join(months)
                month_first = re.search(rf"\b(\d{{1,2}})\s+(?:{month_pattern})\b", normalized)
                month_last = re.search(rf"\b({month_pattern})\s+(\d{{1,2}})\b", normalized)
                if month_first:
                    day = int(month_first.group(1))
                    month = next(
                        months[name]
                        for name in months
                        if re.search(rf"\b{name}\b", month_first.group())
                    )
                    slots["date"] = SlotCandidate(value=f"{month:02d}-{day:02d}", confidence=0.9)
                elif month_last:
                    month = months[month_last.group(1)]
                    day = int(month_last.group(2))
                    slots["date"] = SlotCandidate(value=f"{month:02d}-{day:02d}", confidence=0.9)
    after = re.search(r"(?:после|с)\s+(\d{1,2})(?:[:.]([0-5]\d))?", normalized)
    at = re.search(r"\bв\s+(\d{1,2})(?:[:.]([0-5]\d))?\b", normalized)
    time_match = after or at
    if time_match:
        time_hour, time_minute = time_match.groups()
        value = f"{int(time_hour):02d}:{time_minute or '00'}"
        slots["time_after" if after else "time"] = SlotCandidate(value=value, confidence=0.87)
    else:
        word_time = re.search(
            r"(?:после|с|в)\s+(одного|один|двух|два|тр[её]х|три|"
            r"четыр[её]х|пяти|шести|семи|восьми|девяти|десяти|"
            r"одиннадцати|двенадцати)\b",
            normalized,
        )
        if word_time:
            hour = _HOUR_WORDS[word_time.group(1)]
            is_after = normalized.find("после") >= 0 or normalized.find("с ") >= 0
            if is_after and hour < 12:
                hour += 12
            slots["time_after" if is_after else "time"] = SlotCandidate(
                value=f"{hour:02d}:00", confidence=0.82
            )
    doctor_match = re.search(r"(?:доктор|врач(?:у)?|к)\s+([а-яё-]+\s+[а-яё-]+)", normalized)
    if doctor_match is None:
        doctor_match = re.search(r"\bк\s+([а-яё-]+)\b", normalized)
    if doctor_match:
        doctor_candidate = doctor_match.group(1)
        specialty_words = {variant for variants in _SPECIALTIES.values() for variant in variants}
        pronouns = {"него", "нему", "ней", "ним", "ними", "нее", "этому", "этой"}
        if (
            not any(word in specialty_words for word in doctor_candidate.split())
            and doctor_candidate not in pronouns
        ):
            slots["doctor"] = SlotCandidate(value=doctor_candidate, confidence=0.82)
    clinic_match = re.search(r"\b(?:в|на)\s+филиал\w*\s+([а-яё-]+)", normalized)
    if clinic_match:
        slots["branch"] = SlotCandidate(value=clinic_match.group(1), confidence=0.75)
    city = canonical_city(normalized)
    if city is not None:
        slots["city"] = SlotCandidate(value=city, confidence=0.95)
    phone = re.search(r"(?:\+?\d[\d ()-]{7,}\d)", text)
    if phone:
        slots["phone"] = SlotCandidate(value=re.sub(r"[ ()-]", "", phone.group()), confidence=0.98)
    full_name = re.search(
        r"(?:меня зовут|мо[её] имя(?:\s+и\s+фамил\w*)?|"
        r"для записи имя(?:\s+и\s+фамил\w*)?|"
        r"записываюсь впервые[,. ]+имя(?:\s+и\s+фамил\w*)?)"
        r"\s*[-:—]?\s+([а-яё-]+(?:\s+[а-яё-]+){1,2})",
        normalized,
    )
    if full_name:
        slots["full_name"] = SlotCandidate(value=full_name.group(1).title(), confidence=0.86)
    return NluParse(intent=intent, confidence=confidence, slots=slots)
