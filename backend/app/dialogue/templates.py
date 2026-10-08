"""Assistant replies.

The questions are word-for-word the production phrasing the NLU models
were trained on (ml-training/scripts/dataset_v2/flows.py). The previous
reply is the model context (first 200 chars), so any extra detail must be
appended AFTER the template sentence, never before it.

Naturalness rule: every reply starts with its stable anchor (the exact
training phrase) and varies only in the tail. Tails rotate deterministically
by turn index, so tests stay reproducible. Keep tails short: they still land
inside the 200-char NLU context window.
"""

from collections.abc import Sequence
from datetime import date

from app.dialogue.state import Stage

ASK_SPEC = "Поняла. К какому специалисту хотите записаться?"
ASK_DATE = "На какую дату подобрать время?"
ASK_DATE_TAILS = (
    "Подойдёт словами — «завтра», «в понедельник» — или числом.",
    "Можно и ближайшее свободное окно — так и скажите.",
    "Например: «завтра», «12.10» или «в понедельник».",
)
ASK_CITY = "В каком городе или филиале вам удобнее?"
ASK_CITY_TAILS = (
    "У нас Москва, Санкт-Петербург, Казань и Новосибирск.",
    "Назовите город — например, Москву или Казань.",
)
ASK_PATIENT = "Вы уже были пациентом нашей клиники или записываетесь впервые?"
ASK_PHONE = "Назовите номер телефона, указанный при регистрации, чтобы я нашла вашу карточку."
ASK_NAME = "Как к вам обращаться? Напишите, пожалуйста, имя и фамилию."
NO_SLOTS = (
    "На эту дату свободного времени нет. "
    "Проверить ближайшие доступные окна или выбрать другую дату?"
)
# Prefix for the automatic nearest-windows offer (no extra turn).
NO_SLOTS_AUTO = "На эту дату свободного времени нет. "
SELECT_RECORD = "Какую запись выбрать? Назовите номер варианта."
BOOKED = "Готово, запись оформлена."
BOOKED_TAILS = (
    "Будьте здоровы!",
    "Ждём вас!",
    "Если планы изменятся — просто напишите.",
)
CANCELLED = "Запись отменена."
CANCELLED_TAILS = (
    "Если передумаете — запишу заново.",
    "Обращайтесь!",
)
RESCHEDULED = "Запись перенесена."
RESCHEDULED_TAILS = (
    "Будьте здоровы!",
    "Если что-то ещё понадобится — напишите.",
)
WORKFLOW_CANCELLED = "Хорошо, отменяю текущий запрос. Чем ещё помочь?"
FALLBACK = (
    "Я помогу найти врача и время, записать, перенести или отменить приём. Что вы хотите сделать?"
)
NOT_UNDERSTOOD = (
    "Не совсем поняла. Что вы хотите сделать: записаться, найти врача или проверить запись?"
)
HEALTH = (
    "Понимаю, что вас беспокоит. Я не могу ставить диагноз или выбирать лечение, "
    "но помогу записаться. К какому специалисту хотите обратиться?"
)
THANKS = "Пожалуйста! Если понадобится запись, перенос или отмена — напишите."
THANKS_TAILS = (
    "Всегда рада помочь!",
    "Обращайтесь!",
)
BYE = "До свидания! Будьте здоровы."
BYE_TAILS = (
    "Буду здесь, если понадоблюсь.",
    "Хорошего дня!",
)
GREET_REPLIES = (
    "Здравствуйте! Я консьерж сети клиник. Опишите, что беспокоит, и подскажите город — "
    "подберу клинику.",
    "Здравствуйте! Чем помочь с клиникой — найти врача, свободное время или запись?",
)
PATIENT_NOT_FOUND = (
    "По этому номеру не нашла карточку. Вы записываетесь впервые или хотите проверить номер?"
)

#: First names ending in a soft sign that are feminine (masculine ones like
#: Игорь/Павел are covered by the consonant rule in _looks_feminine).
_FEMININE_SOFT = frozenset({"любовь"})


def _looks_feminine(word: str) -> bool:
    """Heuristic gender guess for a Russian name token."""
    lowered = word.lower()
    return lowered.endswith(("а", "я")) or lowered in _FEMININE_SOFT


def booked_tail(turn: int = 0) -> str:
    return _tail(BOOKED_TAILS, turn)


def cancelled_tail(turn: int = 0) -> str:
    return _tail(CANCELLED_TAILS, turn)


def rescheduled_tail(turn: int = 0) -> str:
    return _tail(RESCHEDULED_TAILS, turn)


def thanks(turn: int = 0) -> str:
    """THANKS anchor plus a rotating warm tail."""
    return f"{THANKS} {_tail(THANKS_TAILS, turn)}"


def bye(turn: int = 0) -> str:
    """BYE anchor plus a rotating warm tail."""
    return f"{BYE} {_tail(BYE_TAILS, turn)}"


def doctors_found(full_name: str) -> str:
    """«Нашла врача …» with the right pronoun (к ней / к нему).

    The surname decides first («Смирнова» → she); gender-neutral surnames
    (Ким, Пак) fall back to the first name («Оксана Пак» → she).
    """
    parts = full_name.split()
    surname = parts[-1] if parts else ""
    first = parts[0] if parts else ""
    pronoun = "ней" if _looks_feminine(surname) or _looks_feminine(first) else "нему"
    return f"Нашла врача {full_name}. Хотите записаться к {pronoun}?"


# Phrases that were not part of the training flows. They are only ever
# produced where a free-form reply is expected or the dialogue ends.
CITY_NOT_SERVED = (
    "В этом городе клиник нет. Назовите другой город: "
    "Москва, Санкт-Петербург, Казань или Новосибирск."
)
SLOT_TAKEN = "Это время уже занято, выберите другое. "
PAST_DATE = "Эта дата уже прошла. " + ASK_DATE
NO_RECORDS = "Активных записей не нашла. Хотите записаться на приём?"
NO_DOCTORS = "В этой клинике такого врача не нашла. Назовите другого врача или специальность."
ERROR = (
    "Не получилось выполнить запрос. "
    "Что вы хотите сделать: записаться, найти врача или проверить запись?"
)

_GENITIVE = {
    "cardiology": "кардиолога",
    "dermatology": "дерматолога",
    "neurology": "невролога",
    "pediatrics": "педиатра",
    "dentistry": "стоматолога",
    "surgery": "хирурга",
    "otolaryngology": "ЛОР-врача",
    "ophthalmology": "офтальмолога",
    "therapist": "терапевта",
    "critical_care": "реаниматолога",
}


def specialty_genitive(specialty: str) -> str:
    return _GENITIVE.get(specialty, specialty)


def _tail(tails: Sequence[str], turn: int) -> str:
    """Deterministic tail rotation: same turn index, same tail."""
    return tails[turn % len(tails)]


def ask_date(turn: int = 0) -> str:
    """ASK_DATE anchor plus a rotating hint tail."""
    return f"{ASK_DATE} {_tail(ASK_DATE_TAILS, turn)}"


def ask_city(turn: int = 0) -> str:
    """ASK_CITY anchor plus a rotating hint tail."""
    return f"{ASK_CITY} {_tail(ASK_CITY_TAILS, turn)}"


def ask_city_spec(specialty: str | None, turn: int = 0) -> str:
    if specialty in _GENITIVE:
        tails = (
            "В каком городе или филиале? Например, Москва или Казань.",
            "Подскажите город — проверю окна.",
            "В каком городе искать? У нас Москва, Петербург, Казань и Новосибирск.",
        )
        return f"Да, ищу {_GENITIVE[specialty]}. {_tail(tails, turn)}"
    return ask_city(turn)


def spec_na(specialty: str) -> str:
    return (
        f"В выбранной клинике нет {specialty_genitive(specialty)}. "
        "Назовите другой филиал или город — проверю, где он принимает."
    )


REPEAT_PREFIX = "Не совсем поняла. "

#: Stage-aware clarification hints for the second repeat (level 2).
REPEAT_HINTS: dict[str, str] = {
    "ask_specialty": "Например: кардиолог, невролог, стоматолог.",
    "ask_date": "Дату можно словами — «завтра», «в понедельник» — или числом.",
    "ask_city": "Подойдут Москва, Санкт-Петербург, Казань или Новосибирск.",
    "ask_patient": "Ответьте «впервые» или «уже был».",
    "ask_phone": "Достаточно цифр — например, 89150001122.",
    "ask_name": "Нужны имя и фамилия — например, Иван Петров.",
    "select_slot": "Назовите номер варианта — 1, 2, 3…",
    "select_record": "Назовите номер записи.",
    "confirm": "Ответьте «да» или «нет».",
}
REPEAT_HINT_DEFAULT = "Опишите коротко, что нужно."
REPEAT_RESTART = (
    "Давайте начнём заново. Что вы хотите сделать: записаться, найти врача "
    "или проверить запись? Скажите «отмена», и начнём с чистого листа."
)

#: Stages whose question is a stable anchor (safe to repeat verbatim on level 2).
_ANCHORED_STAGES: dict[str, str] = {
    "ask_specialty": ASK_SPEC,
    "ask_date": ASK_DATE,
    "ask_city": ASK_CITY,
    "ask_patient": ASK_PATIENT,
    "ask_phone": ASK_PHONE,
    "ask_name": ASK_NAME,
}


def repeat(previous_reply: str) -> str:
    """«Не совсем поняла. » + the previous question (prefix never stacks)."""
    previous = previous_reply
    while previous.startswith(REPEAT_PREFIX):
        previous = previous.removeprefix(REPEAT_PREFIX)
    return REPEAT_PREFIX + previous


def repeat_escalated(stage: Stage, previous_reply: str, level: int) -> str:
    """Level 1: plain repeat. Level 2: anchor + stage hint. Level 3+: restart offer."""
    if level <= 1:
        return repeat(previous_reply)
    if level == 2:
        anchor = _ANCHORED_STAGES.get(stage.value, "")
        base = (REPEAT_PREFIX + anchor) if anchor else repeat(previous_reply)
        return f"{base} {REPEAT_HINTS.get(stage.value, REPEAT_HINT_DEFAULT)}"
    return REPEAT_RESTART


def dm(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}"


_WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_WEEKDAY_ON = (
    "в понедельник",
    "во вторник",
    "в среду",
    "в четверг",
    "в пятницу",
    "в субботу",
    "в воскресенье",
)


def dw_short(day: date) -> str:
    """Short weekday for slot listings: «09.10 (пт)»."""
    return _WEEKDAY_SHORT[day.weekday()]


def dw_on(day: date) -> str:
    """Day phrase for speech: «в пятницу», «во вторник»."""
    return _WEEKDAY_ON[day.weekday()]


def slots_reply(options: Sequence[tuple[date, str]]) -> str:
    parts = "; ".join(
        f"{i + 1} — {dm(day)} ({dw_short(day)}) в {hm}" for i, (day, hm) in enumerate(options)
    )
    return f"Доступное время: {parts}; Какой вариант выбрать?"


_STATUS_RU = {"booked": "запись активна", "cancelled": "отменена", "completed": "приём состоялся"}


def records_reply(records: Sequence[tuple[date, str, str, str]]) -> str:
    """records: (day, HH:MM, status, doctor)."""
    parts = "; ".join(
        f"{dm(day)} ({dw_short(day)}) в {hm} — {_STATUS_RU.get(status, status)}, {doctor}"
        for day, hm, status, doctor in records
    )
    return f"Нашла записи: {parts}."


def preview_reply(day: date, hm: str) -> str:
    return (
        f"Ближайшее время: {dw_on(day)}, {dm(day)} в {hm}. "
        "Если хотите записаться, скажите «запишите меня»."
    )


def confirm_book(day: date, hm: str, new_patient_name: str | None) -> str:
    when = f"{dm(day)} в {hm}"
    if new_patient_name:
        return (
            f"Создать профиль «{new_patient_name}» и оформить запись на {when}? "
            "Скажите «да» или «нет»."
        )
    return f"Записать вас на {when}? Скажите «да» или «нет»."


def confirm_cancel(day: date, hm: str) -> str:
    return f"Отменить запись на {dm(day)} в {hm}? Скажите «да» или «нет»."


def confirm_reschedule(day: date, hm: str) -> str:
    return f"Перенести запись на {dm(day)} в {hm}? Скажите «да» или «нет»."


def clinics_reply(clinics: Sequence[tuple[str, str | None]]) -> str:
    """clinics: (name, address)."""
    parts = "; ".join(f"{name}, {address}" if address else name for name, address in clinics)
    return f"Нашла клиники: {parts}. Что вы хотите сделать: записаться или найти врача?"


NO_SLOTS_NEAR = "В ближайшие дни свободного времени нет. " + ASK_DATE
