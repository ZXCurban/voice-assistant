"""Assistant replies.

The questions are word-for-word the production phrasing the NLU models
were trained on (ml-training/scripts/dataset_v2/flows.py). The previous
reply is the model context (first 200 chars), so any extra detail must be
appended AFTER the template sentence, never before it.
"""

from collections.abc import Sequence
from datetime import date

ASK_SPEC = "Поняла. К какому специалисту хотите записаться?"
ASK_DATE = "На какую дату подобрать время?"
ASK_CITY = "В каком городе или филиале вам удобнее?"
ASK_PATIENT = "Вы уже были пациентом нашей клиники или записываетесь впервые?"
ASK_PHONE = "Назовите номер телефона, указанный при регистрации, чтобы я нашла вашу карточку."
ASK_NAME = "Как к вам обращаться? Напишите, пожалуйста, имя и фамилию."
NO_SLOTS = (
    "На эту дату свободного времени нет. "
    "Проверить ближайшие доступные окна или выбрать другую дату?"
)
SELECT_RECORD = "Какую запись выбрать? Назовите номер варианта."
BOOKED = "Готово, запись оформлена."
CANCELLED = "Запись отменена."
RESCHEDULED = "Запись перенесена."
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
BYE = "До свидания! Будьте здоровы."
GREET_REPLIES = (
    "Здравствуйте! Я консьерж сети клиник. Опишите, что беспокоит, и подскажите город — "
    "подберу клинику.",
    "Здравствуйте! Чем помочь с клиникой — найти врача, свободное время или запись?",
)
PATIENT_NOT_FOUND = (
    "По этому номеру не нашла карточку. Вы записываетесь впервые или хотите проверить номер?"
)
DOCTORS_FOUND = "Нашла врача {doc}. Хотите записаться к нему?"

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


def ask_city_spec(specialty: str | None) -> str:
    if specialty in _GENITIVE:
        return (
            f"Да, ищу {_GENITIVE[specialty]}. "
            "В каком городе или филиале? Например, Москва или Казань."
        )
    return ASK_CITY


def spec_na(specialty: str) -> str:
    return (
        f"В выбранной клинике нет {specialty_genitive(specialty)}. "
        "Назовите другой филиал или город — проверю, где он принимает."
    )


def repeat(previous_reply: str) -> str:
    """«Не совсем поняла. » + the previous question (prefix never stacks)."""
    previous = previous_reply
    while previous.startswith("Не совсем поняла. "):
        previous = previous.removeprefix("Не совсем поняла. ")
    return "Не совсем поняла. " + previous


def dm(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}"


def slots_reply(options: Sequence[tuple[date, str]]) -> str:
    parts = "; ".join(f"{i + 1} — {dm(day)} в {hm}" for i, (day, hm) in enumerate(options))
    return f"Доступное время: {parts}; Какой вариант выбрать?"


_STATUS_RU = {"booked": "запись активна", "cancelled": "отменена", "completed": "приём состоялся"}


def records_reply(records: Sequence[tuple[date, str, str, str]]) -> str:
    """records: (day, HH:MM, status, doctor)."""
    parts = "; ".join(
        f"{dm(day)} в {hm} — {_STATUS_RU.get(status, status)}, {doctor}"
        for day, hm, status, doctor in records
    )
    return f"Нашла записи: {parts}."


def preview_reply(day: date, hm: str) -> str:
    return f"Ближайшее время: {dm(day)} в {hm}. Если хотите записаться, скажите «запишите меня»."


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
