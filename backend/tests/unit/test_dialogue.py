"""Dialogue manager scenarios (scripted NLU + in-memory backend, no DB)."""

from datetime import UTC, datetime

from app.dialogue import templates as t
from app.dialogue.emergency import EMERGENCY_REPLY
from app.dialogue.manager import DialogueManager
from app.dialogue.state import DialogueState, Stage
from tests.unit.dialogue_fakes import FakeBackend, Script, ScriptedEngine

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)  # Thursday, 12:00 in Warsaw


class Chat:
    """One conversation: user text in, assistant reply out."""

    def __init__(self, script: Script, *, confidence: float = 0.99) -> None:
        self.backend = FakeBackend()
        self.engine = ScriptedEngine(script, confidence=confidence)
        self.state = DialogueState()
        self.manager = DialogueManager(self.engine, self.backend.execute, clock=lambda: NOW)

    async def say(self, text: str) -> str:
        reply = await self.manager.handle(self.state, text)
        assert reply is not None
        return reply


BOOK_SCRIPT: Script = {
    "хочу записаться к кардиологу": ("book_appointment", {"specialty": "cardiology"}),
    "завтра": ("unknown_request", {"date": "tomorrow"}),
    "сегодня": ("unknown_request", {"date": "today"}),
    "в Варшаве": ("unknown_request", {"city": "warszawa"}),
    "в Лиссабоне": ("unknown_request", {"city": "lisboa"}),
    "второй": ("select_option", {"selection": "2"}),
    "самый ранний": ("select_option", {"selection": "earliest"}),
    "на 15:00": ("select_option", {"selection_time": "15:00"}),
    "впервые": ("unknown_request", {"patient_mode": "new"}),
    "я уже был": ("unknown_request", {"patient_mode": "registered"}),
    "Иван Сидоров": ("unknown_request", {"full_name": "Иван Сидоров"}),
    "+48 501 234 568": ("unknown_request", {"phone": "+48501234568"}),
    "+48 501 234 567": ("unknown_request", {"phone": "+48501234567"}),
    "+48 700 000 000": ("unknown_request", {"phone": "+48700000000"}),
    "да": ("confirm", {}),
    "нет": ("reject", {}),
}


async def _to_slot_list(chat: Chat) -> str:
    assert await chat.say("хочу записаться к кардиологу") == t.ASK_DATE
    assert await chat.say("завтра") == t.ask_city_spec("cardiology")
    return await chat.say("в Варшаве")


async def test_new_patient_books_with_confirmation() -> None:
    chat = Chat(BOOK_SCRIPT)
    listing = await _to_slot_list(chat)
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00; 2 — 09.10 в 09:30;")
    assert listing.endswith("Какой вариант выбрать?")
    assert await chat.say("второй") == t.ASK_PATIENT
    assert await chat.say("впервые") == t.ASK_NAME
    assert await chat.say("Иван Сидоров") == t.ASK_PHONE
    question = await chat.say("+48 501 234 568")
    assert question.startswith(
        "Создать профиль «Иван Сидоров» и оформить запись на 09.10 в 09:30? Скажите «да» или «нет»."
    )
    assert "Jan Kowalski" in question
    # Nothing is written before the user says «да».
    assert chat.backend.appointments == []
    assert "book_appointment" not in chat.backend.tool_names()

    reply = await chat.say("да")
    assert reply == t.BOOKED + " Jan Kowalski, 09.10 в 09:30."
    (appointment,) = chat.backend.appointments
    assert appointment["status"] == "booked"
    assert appointment["starts_at"] == chat.backend.instant(
        1, datetime(2026, 10, 9).date(), "09:30"
    )
    assert chat.backend.patients[-1]["full_name"] == "Иван Сидоров"
    assert chat.state.stage is Stage.IDLE


async def test_model_sees_production_phrasing_as_context() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("второй")
    await chat.say("впервые")
    contexts = chat.engine.contexts
    assert contexts[0] == ""
    assert contexts[1] == t.ASK_DATE
    assert contexts[2] == t.ask_city_spec("cardiology")
    assert contexts[3].startswith("Доступное время: 1 — 09.10 в 09:00")
    assert contexts[4] == t.ASK_PATIENT
    assert len(contexts) == 5


async def test_registered_patient_gets_backend_preview_then_books() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    assert await chat.say("самый ранний") == t.ASK_PATIENT
    assert await chat.say("я уже был") == t.ASK_PHONE
    question = await chat.say("+48 501 234 567")
    assert question.startswith("Записать вас на 09.10 в 09:00? Скажите «да» или «нет».")
    names = chat.backend.tool_names()
    assert names[-2:] == ["find_patient", "book_appointment"]
    assert chat.backend.calls[-1][1]["confirmed"] is False
    assert (await chat.say("да")).startswith(t.BOOKED)
    assert chat.backend.appointments[0]["patient_id"] == 1


async def test_unknown_registered_phone_is_not_a_dead_end() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("я уже был")
    assert await chat.say("+48 700 000 000") == t.PATIENT_NOT_FOUND
    assert await chat.say("впервые") == t.ASK_NAME


async def test_rejecting_the_confirmation_cancels_the_flow() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("впервые")
    await chat.say("Иван Сидоров")
    await chat.say("+48 501 234 568")
    assert await chat.say("нет") == t.WORKFLOW_CANCELLED
    assert chat.backend.appointments == []
    assert chat.state.stage is Stage.IDLE


async def test_time_can_be_chosen_beyond_the_five_shown_options() -> None:
    chat = Chat(BOOK_SCRIPT)
    listing = await _to_slot_list(chat)
    assert "15:00" not in listing  # only the first five are read out
    assert await chat.say("на 15:00") == t.ASK_PATIENT
    assert chat.state.chosen_slot is not None
    assert chat.state.chosen_slot.hm == "15:00"


async def test_specialty_missing_in_clinic_asks_for_another_city() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "запишите к дерматологу на завтра": (
            "book_appointment",
            {"specialty": "dermatology", "date": "tomorrow"},
        ),
    }
    chat = Chat(script)
    assert await chat.say("запишите к дерматологу на завтра") == t.ask_city_spec("dermatology")
    assert await chat.say("в Лиссабоне") == t.spec_na("dermatology")
    assert chat.state.stage is Stage.ASK_CITY
    listing = await chat.say("в Варшаве")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")


async def test_no_slots_offers_nearest_windows() -> None:
    chat = Chat(BOOK_SCRIPT)
    await chat.say("хочу записаться к кардиологу")
    await chat.say("сегодня")
    assert await chat.say("в Варшаве") == t.NO_SLOTS
    listing = await chat.say("да")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")


async def test_time_after_filters_the_offered_slots() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "завтра после двух": ("unknown_request", {"date": "tomorrow", "time_after": "14:00"}),
    }
    chat = Chat(script)
    await chat.say("хочу записаться к кардиологу")
    await chat.say("завтра после двух")
    listing = await chat.say("в Варшаве")
    assert (
        listing == "Доступное время: 1 — 09.10 в 14:00; 2 — 09.10 в 14:30; 3 — 09.10 в 15:00; "
        "Какой вариант выбрать?"
    )


async def test_nearest_flow_skips_the_date_question() -> None:
    script: Script = {
        "найдите ближайшее время к кардиологу": ("find_nearest_slots", {"specialty": "cardiology"}),
        "в Варшаве": ("unknown_request", {"city": "warszawa"}),
        "второй": ("select_option", {"selection": "2"}),
        "запишите меня": ("book_appointment", {}),
    }
    chat = Chat(script)
    assert await chat.say("найдите ближайшее время к кардиологу") == t.ask_city_spec("cardiology")
    listing = await chat.say("в Варшаве")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")
    assert await chat.say("второй") == t.preview_reply(chat.state.chosen_slot.day, "09:30")  # type: ignore[union-attr]
    assert await chat.say("запишите меня") == t.ASK_PATIENT


async def test_slot_taken_before_confirmation_offers_new_list() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("я уже был")
    await chat.say("+48 501 234 567")
    taken = chat.state.chosen_slot
    assert taken is not None
    chat.backend.busy.add((taken.doctor.id, taken.starts_at))
    reply = await chat.say("да")
    assert reply.startswith(t.SLOT_TAKEN + "Доступное время: 1 — 09.10 в 09:30")
    assert chat.backend.appointments == []
    assert chat.state.stage is Stage.SELECT_SLOT


async def test_doctor_named_by_surname_in_cyrillic() -> None:
    script: Script = {
        "запишите к Ковальскому": ("book_appointment", {"doctor": "Ковальский"}),
        "завтра": ("unknown_request", {"date": "tomorrow"}),
        "в Варшаве": ("unknown_request", {"city": "warszawa"}),
    }
    chat = Chat(script)
    assert await chat.say("запишите к Ковальскому") == t.ASK_DATE
    assert await chat.say("завтра") == t.ASK_CITY.replace("вам удобнее", "вам удобнее")
    listing = await chat.say("в Варшаве")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")
    assert chat.state.doctor is not None and chat.state.doctor.id == 11


async def test_unknown_doctor_is_reported() -> None:
    script: Script = {
        "запишите к Иванову": ("book_appointment", {"doctor": "Иванов"}),
        "завтра": ("unknown_request", {"date": "tomorrow"}),
        "в Варшаве": ("unknown_request", {"city": "warszawa"}),
    }
    chat = Chat(script)
    await chat.say("запишите к Иванову")
    await chat.say("завтра")
    assert await chat.say("в Варшаве") == t.NO_DOCTORS


async def _book_one(chat: Chat) -> None:
    """Pre-book 09:30 tomorrow for Ivan Petrov (patient 1)."""
    day = datetime(2026, 10, 9).date()
    chat.backend.appointments.append(
        {
            "id": 1,
            "clinic_id": 1,
            "doctor_id": 11,
            "patient_id": 1,
            "starts_at": chat.backend.instant(1, day, "09:30"),
            "status": "booked",
        }
    )


CANCEL_SCRIPT: Script = {
    "отмените запись": ("cancel_appointment", {}),
    "перенесите запись": ("reschedule_appointment", {}),
    "какие у меня записи": ("get_appointments", {}),
    "Варшава": ("unknown_request", {"city": "warszawa"}),
    "я уже был": ("unknown_request", {"patient_mode": "registered"}),
    "+48 501 234 567": ("unknown_request", {"phone": "+48501234567"}),
    "да": ("confirm", {}),
    "нет": ("reject", {}),
    "первую": ("select_option", {"selection": "1"}),
    "на понедельник": ("unknown_request", {"date": "weekday:0"}),
    "второй": ("select_option", {"selection": "2"}),
}


async def _login(chat: Chat) -> str:
    # Only registered patients have appointments: no «first time?» question here.
    assert await chat.say("Варшава") == t.ASK_PHONE
    return await chat.say("+48 501 234 567")


async def test_cancel_single_appointment() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await _book_one(chat)
    assert await chat.say("отмените запись") == t.ASK_CITY
    question = await _login(chat)
    assert question.startswith("Отменить запись на 09.10 в 09:30? Скажите «да» или «нет».")
    assert chat.backend.appointments[0]["status"] == "booked"
    assert await chat.say("да") == t.CANCELLED
    assert chat.backend.appointments[0]["status"] == "cancelled"


async def test_cancel_declined_keeps_the_appointment() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await _book_one(chat)
    await chat.say("отмените запись")
    await _login(chat)
    assert await chat.say("нет") == t.WORKFLOW_CANCELLED
    assert chat.backend.appointments[0]["status"] == "booked"


async def test_cancel_with_several_appointments_asks_which() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await _book_one(chat)
    chat.backend.appointments.append(
        {
            "id": 2,
            "clinic_id": 1,
            "doctor_id": 11,
            "patient_id": 1,
            "starts_at": chat.backend.instant(1, datetime(2026, 10, 12).date(), "10:00"),
            "status": "booked",
        }
    )
    await chat.say("отмените запись")
    reply = await _login(chat)
    assert reply.startswith(
        "Нашла записи: 09.10 в 09:30 — запись активна, Jan Kowalski; 12.10 в 10:00"
    )
    assert reply.endswith(t.SELECT_RECORD)
    question = await chat.say("второй")
    assert question.startswith("Отменить запись на 12.10 в 10:00?")
    await chat.say("да")
    assert [a["status"] for a in chat.backend.appointments] == ["booked", "cancelled"]


async def test_reschedule_moves_the_appointment() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await _book_one(chat)
    await chat.say("перенесите запись")
    assert await _login(chat) == t.ASK_DATE
    listing = await chat.say("на понедельник")
    assert listing.startswith("Доступное время: 1 — 12.10 в 09:00")
    question = await chat.say("первую")
    assert question.startswith("Перенести запись на 12.10 в 09:00? Скажите «да» или «нет».")
    assert (await chat.say("да")).startswith(t.RESCHEDULED)
    assert chat.backend.appointments[0]["starts_at"] == chat.backend.instant(
        1, datetime(2026, 10, 12).date(), "09:00"
    )


async def test_records_are_listed_without_any_mutation() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await _book_one(chat)
    await chat.say("какие у меня записи")
    assert await _login(chat) == "Нашла записи: 09.10 в 09:30 — запись активна, Jan Kowalski."
    assert chat.state.stage is Stage.IDLE
    assert not {"cancel_appointment", "reschedule_appointment"} & set(chat.backend.tool_names())


async def test_patient_without_records_is_offered_a_booking() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await chat.say("какие у меня записи")
    reply = await _login(chat)
    assert reply == t.NO_RECORDS
    assert chat.state.stage is Stage.OFFER_BOOK


async def test_known_clinic_and_patient_are_reused_in_the_next_flow() -> None:
    chat = Chat(CANCEL_SCRIPT)
    await chat.say("какие у меня записи")
    await _login(chat)
    chat.backend.calls.clear()
    assert await chat.say("какие у меня записи") == t.NO_RECORDS
    assert chat.backend.tool_names() == ["get_appointments"]


async def test_emergency_is_answered_before_the_nlu() -> None:
    chat = Chat(BOOK_SCRIPT)
    assert await chat.say("у меня сильная боль в груди") == EMERGENCY_REPLY
    assert chat.engine.contexts == []


async def test_health_concern_leads_to_a_specialty_question() -> None:
    script: Script = {
        "болит голова": ("health_concern", {}),
        "к неврологу": ("unknown_request", {"specialty": "neurology"}),
    }
    chat = Chat(script)
    assert await chat.say("болит голова") == t.HEALTH
    assert await chat.say("к неврологу") == t.ASK_DATE


async def test_greeting_unintelligible_and_repeat() -> None:
    chat = Chat({"здравствуйте": ("greeting", {}), "хочу записаться": ("book_appointment", {})})
    assert await chat.say("здравствуйте") == t.GREET_REPLIES[0]
    assert await chat.say("абракадабра") == t.NOT_UNDERSTOOD
    assert await chat.say("хочу записаться") == t.ASK_SPEC
    assert await chat.say("абракадабра") == t.repeat(t.ASK_SPEC)
    assert await chat.say("ещё абракадабра") == t.repeat(t.ASK_SPEC)  # prefix never stacks


async def test_unintelligible_turn_can_be_deferred_to_the_llm() -> None:
    chat = Chat({})
    assert await chat.manager.handle(chat.state, "абракадабра", allow_defer=True) is None
    assert chat.state.last_response == ""


async def test_low_confidence_confirm_falls_back_to_plain_yes() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("впервые")
    await chat.say("Иван Сидоров")
    await chat.say("+48 501 234 568")
    # «ага» is not in the script: the model is unsure, the yes/no rule decides.
    assert (await chat.say("ага")).startswith(t.BOOKED)


async def test_phone_and_name_are_extracted_without_the_model() -> None:
    chat = Chat(BOOK_SCRIPT)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("впервые")
    assert await chat.say("меня зовут Пётр Иванов") == t.ASK_PHONE
    question = await chat.say("мой номер 48 501 234 569")
    assert question.startswith("Создать профиль «Пётр Иванов»")
    assert chat.state.patient_phone == "48501234569"


async def test_city_without_clinic_is_rejected() -> None:
    chat = Chat(BOOK_SCRIPT)
    chat.backend.clinics = [c for c in chat.backend.clinics if c["city"] != "Lisboa"]
    await chat.say("хочу записаться к кардиологу")
    await chat.say("завтра")
    assert await chat.say("в Лиссабоне") == t.CITY_NOT_SERVED


async def test_find_clinics_without_city_lists_every_clinic() -> None:
    chat = Chat({"какие у вас клиники": ("find_clinics", {})})
    reply = await chat.say("какие у вас клиники")
    assert "Przychodnia Srodmiescie" in reply and "Clinica Lisboa" in reply


async def test_find_clinics_by_city_lists_only_that_city() -> None:
    script: Script = {"клиники в Варшаве": ("find_clinics", {"city": "warszawa"})}
    chat = Chat(script)
    reply = await chat.say("клиники в Варшаве")
    assert reply.startswith("Нашла клиники: Przychodnia Srodmiescie, Marszalkowska 1, Warszawa.")
    assert "Lisboa" not in reply


async def test_find_doctors_then_book_with_the_found_doctor() -> None:
    script: Script = {
        "есть ли кардиолог": ("find_doctors", {"specialty": "cardiology"}),
        "Варшава": ("unknown_request", {"city": "warszawa"}),
        "да": ("confirm", {}),
        "завтра": ("unknown_request", {"date": "tomorrow"}),
    }
    chat = Chat(script)
    assert await chat.say("есть ли кардиолог") == t.ask_city_spec("cardiology")
    assert await chat.say("Варшава") == t.DOCTORS_FOUND.format(doc="Jan Kowalski")
    assert await chat.say("да") == t.ASK_DATE
    listing = await chat.say("завтра")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")
    assert chat.state.doctor is not None and chat.state.doctor.id == 11


async def test_bare_no_to_the_patient_question_means_first_visit() -> None:
    script: Script = {**BOOK_SCRIPT, "нет, не был": ("reject", {})}
    chat = Chat(script)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    assert await chat.say("нет, не был") == t.ASK_NAME
    assert chat.state.patient_mode == "new"


async def test_cancel_go_ahead_is_not_read_as_a_refusal() -> None:
    script: Script = {**CANCEL_SCRIPT, "отменяйте": ("reject", {})}
    chat = Chat(script)
    await _book_one(chat)
    await chat.say("отмените запись")
    await _login(chat)
    assert await chat.say("отменяйте") == t.CANCELLED
    assert chat.backend.appointments[0]["status"] == "cancelled"


async def test_another_date_after_no_free_time_asks_for_the_date() -> None:
    script: Script = {**BOOK_SCRIPT, "давайте другую дату": ("reject", {})}
    chat = Chat(script)
    await chat.say("хочу записаться к кардиологу")
    await chat.say("сегодня")
    assert await chat.say("в Варшаве") == t.NO_SLOTS
    assert await chat.say("давайте другую дату") == t.ASK_DATE
    assert (await chat.say("завтра")).startswith("Доступное время: 1 — 09.10 в 09:00")


async def test_model_phone_that_was_not_said_is_dropped_and_regex_wins() -> None:
    # The model dropped a digit («+48 501 234 56»); the regex finds the real number.
    script: Script = {
        **CANCEL_SCRIPT,
        "мой номер +48 501 234 567": ("unknown_request", {"phone": "+4850123456"}),
    }
    chat = Chat(script)
    await _book_one(chat)
    await chat.say("отмените запись")
    await chat.say("Варшава")
    reply = await chat.say("мой номер +48 501 234 567")
    assert reply.startswith("Отменить запись на 09.10 в 09:30?")


async def test_invented_name_is_not_taken_from_the_model() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "ну как вас там": ("unknown_request", {"full_name": "Пётр Иванов"}),
    }
    chat = Chat(script)
    await _to_slot_list(chat)
    await chat.say("самый ранний")
    await chat.say("впервые")
    assert (await chat.say("ну как вас там")).endswith(t.ASK_NAME)
    assert chat.state.patient_name is None


async def test_local_phone_without_country_code_finds_the_patient() -> None:
    script: Script = {
        **CANCEL_SCRIPT,
        "501234567": ("unknown_request", {"phone": "501234567"}),
    }
    chat = Chat(script)
    await _book_one(chat)
    await chat.say("отмените запись")
    await chat.say("Варшава")
    assert (await chat.say("501234567")).startswith("Отменить запись на 09.10 в 09:30?")


async def test_exact_time_matching_one_slot_is_chosen_immediately() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "запишите к кардиологу в Варшаве завтра на 14:00": (
            "book_appointment",
            {"specialty": "cardiology", "city": "warszawa", "date": "tomorrow", "time": "14:00"},
        ),
    }
    chat = Chat(script)
    assert await chat.say("запишите к кардиологу в Варшаве завтра на 14:00") == t.ASK_PATIENT
    assert chat.state.chosen_slot is not None
    assert chat.state.chosen_slot.hm == "14:00"


async def test_thanks_and_goodbye_get_a_polite_answer() -> None:
    chat = Chat({})
    assert await chat.say("спасибо") == t.THANKS
    assert await chat.say("до свидания") == t.BYE
    assert await chat.say("что ты умеешь?") == t.FALLBACK


async def test_date_answer_does_not_change_the_specialty() -> None:
    # The model hallucinated «ophthalmology» for the typo «завтро».
    script: Script = {
        **BOOK_SCRIPT,
        "завтро": ("unknown_request", {"specialty": "ophthalmology", "date": "tomorrow"}),
    }
    chat = Chat(script)
    await chat.say("хочу записаться к кардиологу")
    assert await chat.say("завтро") == t.ask_city_spec("cardiology")
    assert chat.state.wants["specialty"] == "cardiology"


async def test_doctor_named_in_the_dative_case() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "к доктору Ковальскому": ("book_appointment", {"doctor": "Ковальскому"}),
    }
    chat = Chat(script)
    await chat.say("к доктору Ковальскому")
    await chat.say("завтра")
    listing = await chat.say("в Варшаве")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")


async def test_misspelled_specialty_beats_a_wrong_model_guess() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "записатся к кордиологу": ("book_appointment", {"specialty": "ophthalmology"}),
    }
    chat = Chat(script)
    await chat.say("записатся к кордиологу")
    assert chat.state.wants["specialty"] == "cardiology"


async def test_misspelled_relative_date_overrides_the_models_guess() -> None:
    script: Script = {
        **BOOK_SCRIPT,
        "завтро": ("unknown_request", {"date": "day_after_tomorrow"}),
    }
    chat = Chat(script)
    await chat.say("хочу записаться к кардиологу")
    await chat.say("завтро")
    listing = await chat.say("в Варшаве")
    assert listing.startswith("Доступное время: 1 — 09.10 в 09:00")
