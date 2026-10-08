"""Emergency pre-filter: runs BEFORE the NLU, which labels such phrases
as unknown_request and would answer with a booking question."""

import re
import unicodedata

EMERGENCY_REPLY = (
    "Если это экстренная ситуация, немедленно звоните 112 или вызывайте скорую помощь. "
    "Я помогаю только с записью на приём и не заменяю неотложную помощь."
)

_EMERGENCY = re.compile(
    r"не\s+могу\s+дышать|трудно\s+дышать|нечем\s+дышать|задыхаюсь|не\s+дышит|"
    r"сердечн\w+\s+приступ|инфаркт|инсульт|"
    r"потерял[аи]?\s+сознание|без\s+сознания|теряю\s+сознание|обморок|"
    r"сильн\w+\s+боль\s+в\s+груди|боль\s+в\s+груди\s+и|"
    r"сильн\w+\s+кровотечени|кровотечени\w+\s+не\s+останавлива|"
    r"истека\w+\s+кровью|теря\w+\s+кровь|много\s+крови|"
    r"кровь\s+(хлещет|льет|льёт|льется|льётся|фонтаном|не\s+останавлива)|"
    r"кровотечение|"
    r"остановк\w+\s+сердца|отравил\w+|судорог|"
    r"умира\w+|при\s+смерти|"
    r"скор\w+\s+помощь|вызывай\w*\s+(скор\w+|врача)|вызов\w*\s+скор\w+|"
    r"звони\w*\s+(в\s+)?скор\w+|"
    r"хочу\s+умереть|покончить\s+с\s+собой|не\s+хочу\s+жить"
)


def is_emergency(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return _EMERGENCY.search(normalized) is not None
