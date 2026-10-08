"""NLU glue: contract validators, deterministic extractors, value mapping."""

from datetime import date

import pytest

from app.nlu.contract import (
    build_model_input,
    clean_slots,
    parse_slots_linear,
)
from app.nlu.fallbacks import (
    detect_patient_mode,
    detect_relative_date,
    detect_smalltalk,
    detect_specialty,
    detect_specialty_fuzzy,
    detect_yes_no,
    extract_full_name,
    extract_phone,
    name_in_text,
    phone_in_text,
)
from app.nlu.values import (
    match_doctors,
    normalize_phone,
    phone_variants,
    resolve_date,
    transliterate,
)

THURSDAY = date(2026, 10, 8)


def test_model_input_matches_the_training_format() -> None:
    assert build_model_input("Здравствуйте") == "Здравствуйте"
    assert build_model_input("завтра", "На какую дату?") == "На какую дату? [SEP] завтра"
    long_reply = "а" * 300
    assert build_model_input("да", long_reply) == "а" * 200 + " [SEP] да"


def test_linear_slots_are_parsed_and_cleaned() -> None:
    raw = parse_slots_linear("date=tomorrow; time_after=14:00; specialty=cardiology")
    assert raw == {"date": "tomorrow", "time_after": "14:00", "specialty": "cardiology"}
    assert parse_slots_linear("none") == {}
    assert parse_slots_linear("") == {}
    assert parse_slots_linear("garbage; k=; =v") == {}


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({"specialty": "gynecology"}, {}),  # outside the contract
        ({"city": "krakow"}, {}),
        ({"city": "warszawa"}, {}),  # old training vocabulary is no longer valid
        ({"time": "25:00"}, {}),
        ({"date": "next month"}, {}),
        ({"date": "2026-13-40"}, {}),
        ({"phone": "123"}, {}),
        ({"full_name": "иван"}, {}),  # needs two capitalized words
        ({"unknown_slot": "x"}, {}),
        ({"date": "weekday:4", "time": "09:30"}, {"date": "weekday:4", "time": "09:30"}),
        ({"phone": "+7 921-000-00-01"}, {"phone": "+79210000001"}),
        ({"city": "moskva"}, {"city": "moskva"}),
        ({"full_name": "Иван Петров"}, {"full_name": "Иван Петров"}),
    ],
)
def test_clean_slots_enforces_the_contract(slots: dict[str, str], expected: dict[str, str]) -> None:
    assert clean_slots(slots) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("today", date(2026, 10, 8)),
        ("tomorrow", date(2026, 10, 9)),
        ("day_after_tomorrow", date(2026, 10, 10)),
        ("in_days:5", date(2026, 10, 13)),
        ("weekday:0", date(2026, 10, 12)),  # next Monday
        ("weekday:3", date(2026, 10, 15)),  # today is Thursday: next week, not today
        ("next_week:1", date(2026, 10, 13)),  # Tuesday of next week
        ("10-20", date(2026, 10, 20)),
        ("10-01", date(2027, 10, 1)),  # already passed: next year
        ("2026-12-24", date(2026, 12, 24)),
        ("02-30", None),
        ("someday", None),
    ],
)
def test_resolve_date(value: str, expected: date | None) -> None:
    assert resolve_date(value, THURSDAY) == expected


def test_doctor_surnames_match_across_alphabets() -> None:
    names = ["Андрей Волков", "Ольга Морозова", "Елена Кузнецова"]
    assert match_doctors("Волков", names) == [0]
    assert match_doctors("Морозова", names) == [1]
    assert match_doctors("Кузнецова", names) == [2]
    assert match_doctors("volkov", names) == [0]
    assert match_doctors("Иванов", names) == []
    assert match_doctors("", names) == []
    assert transliterate("Щука") == "schuka"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("мой номер +7 921 000 00 01", "+79210000001"),
        ("8 (999) 123-45-67", "89991234567"),
        ("мне 25 лет", None),
        ("12345", None),
    ],
)
def test_extract_phone(text: str, expected: str | None) -> None:
    assert extract_phone(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Иван Петров", "Иван Петров"),
        ("меня зовут Анна Ковальская.", "Анна Ковальская"),
        ("анна ковальская", "Анна Ковальская"),
        ("Иван", None),
        ("запишите меня завтра в десять", None),
    ],
)
def test_extract_full_name(text: str, expected: str | None) -> None:
    assert extract_full_name(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("к кардиологу", "cardiology"),
        ("нужен кожник", "dermatology"),
        ("ЛОР", "otolaryngology"),
        ("глазного врача", "ophthalmology"),
        ("гинеколог", None),
        ("колорит", None),  # «лор» inside a word is not an ENT doctor
    ],
)
def test_detect_specialty(text: str, expected: str | None) -> None:
    assert detect_specialty(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("впервые", "new"),
        ("нет", "new"),
        ("я у вас первый раз", "new"),
        ("я уже был у вас", "registered"),
        ("была в прошлом году", "registered"),
        ("не была", "new"),
        ("Москва", None),
    ],
)
def test_detect_patient_mode(text: str, expected: str | None) -> None:
    assert detect_patient_mode(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("да", "yes"),
        ("Да, конечно!", "yes"),
        ("ага", "yes"),
        ("пойдет", "yes"),
        ("сойдет", "yes"),
        ("нормально", "yes"),
        ("ладно", "yes"),
        ("запиши давай уже быстрее", "yes"),
        ("дура. да", "yes"),
        ("ну да, записывай", "yes"),
        ("скажите да или нет", None),
        ("нет", "no"),
        ("не надо", "no"),
        ("отмена", "no"),
        (
            "да вообще-то я передумал и хочу другое время сегодня",
            None,
        ),  # too long to be a bare answer
        ("завтра", None),
    ],
)
def test_detect_yes_no(text: str, expected: str | None) -> None:
    assert detect_yes_no(text) == expected


@pytest.mark.parametrize(
    "spoken",
    ["Волкову", "Волкова", "Волковым", "Волков", "volkov"],
)
def test_doctor_surname_matches_in_any_russian_case(spoken: str) -> None:
    assert match_doctors(spoken, ["Андрей Волков", "Ольга Морозова", "Елена Кузнецова"]) == [0]


@pytest.mark.parametrize(
    ("spoken", "index"), [("Морозовой", 1), ("Кузнецовой", 2), ("Соколову", 3)]
)
def test_doctor_surname_stems(spoken: str, index: int) -> None:
    names = ["Андрей Волков", "Ольга Морозова", "Елена Кузнецова", "Игорь Соколов"]
    assert match_doctors(spoken, names) == [index]


def test_unrelated_surname_matches_nobody() -> None:
    assert match_doctors("Иванов", ["Андрей Волков", "Ольга Морозова"]) == []


@pytest.mark.parametrize(
    ("phone", "city", "expected"),
    [
        ("921 000 00 01", "moskva", "+79210000001"),
        ("89210000001", "moskva", "+79210000001"),
        ("89210000001", "kazan", "+79210000001"),
        ("0048 501 234 567", None, "+48501234567"),
        ("+7 921-000-00-01", "novosibirsk", "+79210000001"),
        ("9210000001", "sankt-peterburg", "+79210000001"),
        ("9210000001", None, "9210000001"),
    ],
)
def test_normalize_phone(phone: str, city: str | None, expected: str) -> None:
    assert normalize_phone(phone, city) == expected


def test_phone_variants_try_the_normalized_form_first() -> None:
    assert phone_variants("921 000 00 01", "moskva") == [
        "+79210000001",
        "+9210000001",
        "9210000001",
    ]
    assert phone_variants("+79210000001", "moskva")[0] == "+79210000001"


def test_model_values_are_checked_against_the_text() -> None:
    assert phone_in_text("+79210000001", "мой номер 921 000 00 01")
    assert not phone_in_text("+792100000", "мой номер 921 000 00 01")
    assert name_in_text("Иван Сидоров", "меня зовут иван сидоров")
    assert not name_in_text("Пётр Иванов", "ну как вас там")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("спасибо!", "thanks"),
        ("Большое спасибо", "thanks"),
        ("пока", "bye"),
        ("до свидания", "bye"),
        ("что ты умеешь?", "help"),
        ("спасибо, запишите меня", "thanks"),
        ("запишите к кардиологу", None),
    ],
)
def test_detect_smalltalk(text: str, expected: str | None) -> None:
    assert detect_smalltalk(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("записатся к кордиологу", "cardiology"),
        ("к дерматалогу", "dermatology"),
        ("стаматологу", "dentistry"),
        ("завтро", None),
        ("доктору", None),
        ("здравствуйте", None),
    ],
)
def test_detect_specialty_fuzzy(text: str, expected: str | None) -> None:
    assert detect_specialty_fuzzy(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("завтро", "tomorrow"),
        ("на сегодня вечером", "today"),
        ("после завтра", "day_after_tomorrow"),
        ("послезавтра", "day_after_tomorrow"),
        ("в субботу", None),
        ("здравствуйте", None),
    ],
)
def test_detect_relative_date(text: str, expected: str | None) -> None:
    assert detect_relative_date(text) == expected
