"""Deterministic fastpath: instant Russian smalltalk replies.

Smalltalk (greetings, identity, capabilities, politeness) never needs
a backend round-trip: it is unnecessary and can be answered safely here.
This module is pure (no DB, no network, no LLM) so it is cheap to
unit-test and safe to run before the tool loop.

Contract:
- `match_fastpath()` returns the reply text or None (no match).
- Callers must NOT consult fastpath when a booking confirmation is
  pending (user's «да/нет» answers the preview, not smalltalk).
- Only Russian triggers (per product decision); unknown text → None.
"""

from __future__ import annotations

import re
import unicodedata

_GREETING_RE = re.compile(
    r"^(здравствуй(те)?|привет(ик)?|добрый день|доброе утро|добрый вечер|"
    r"доброго времени суток|hello|hi)\s*[!.…]*$"
)
_WHO_RE = re.compile(
    r"^(кто ты|ты кто|что ты( такое| за бот)?|ты бот|ты робот|ты ии|"
    r"ты искусственный интеллект)\s*[?.!…]*$"
)
_HELP_RE = re.compile(
    r"^((что ты (умеешь|можешь( делать)?))|(твои возможности)|(помощь|помоги)|"
    r"(как (записаться( на прием)?|пользоваться|это работает))|"
    r"(что здесь можно сделать))\s*[?.!…]*$"
)
_THANKS_RE = re.compile(r"^(спасибо( большое)?|благодарю|благодарю вас)\s*[!.…]*$")
_BYE_RE = re.compile(
    r"^(пока( пока)?|до свидания|прощай(те)?|всего (доброго|хорошего)|"
    r"хорошего дня)\s*[!.…]*$"
)
_JUNK_RE = re.compile(r"^([?!.…]+|а\??|э+|м+|ну+)\s*$")

_REPLIES: dict[str, str] = {
    "greeting": (
        "Здравствуйте! Я консьерж сети клиник. "
        "Опишите, что беспокоит, и подскажите город — подберу клинику."
    ),
    "who": (
        "Я AI-консьерж сети клиник: помогаю выбрать клинику, врача и время, "
        "записываю на приём. Диагнозы не ставлю — решение принимает врач на приёме. "
        "Опишите, что беспокоит, и назовите город."
    ),
    "help": (
        "Могу показать клиники и врачей, найти свободное время, "
        "записать на приём, перенести или отменить запись. "
        "Для записи нужны имя, телефон и желаемая дата. "
        "С чего начнём — какая жалоба и какой город?"
    ),
    "thanks": "Пожалуйста! Если понадобится записаться или перенести приём — напишите.",
    "bye": "До свидания! Буду здесь, если понадобится помощь с записью.",
    "junk": (
        "Не совсем понял. Опишите жалобу (например, «болит нога») "
        "и укажите город — так я смогу подобрать клинику."
    ),
}


def _normalize(text: str) -> str:
    """Lowercase, fold ё→е, strip accents/punctuation edges for matching."""
    lowered = unicodedata.normalize("NFKC", text.strip().lower()).replace("ё", "е")
    lowered = re.sub(r"\s+", " ", lowered).strip(" \t\n\r.,;:«»\"'()")
    return lowered


def match_fastpath(message: str, *, has_pending_confirmation: bool = False) -> str | None:
    """Return an instant reply for smalltalk, or None to continue the pipeline.

    When a confirmation preview is pending, always return None so that
    «да/нет/отмена» reach the booking flow instead of a template.
    """
    if has_pending_confirmation:
        return None
    text = _normalize(message)
    if not text or len(text) > 120:
        return None
    if _GREETING_RE.match(text):
        return _REPLIES["greeting"]
    if _WHO_RE.match(text):
        return _REPLIES["who"]
    if _HELP_RE.match(text):
        return _REPLIES["help"]
    if _THANKS_RE.match(text):
        return _REPLIES["thanks"]
    if _BYE_RE.match(text):
        return _REPLIES["bye"]
    if _JUNK_RE.match(text):
        return _REPLIES["junk"]
    return None
