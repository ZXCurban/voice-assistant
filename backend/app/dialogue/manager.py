"""DialogueManager: NLU result + state → backend call → templated reply.

Order of a turn: emergency filter → NLU (intent + slots) → state machine.
The machine asks for what is missing in the same order as the production
assistant the NLU was trained on (specialty → date → city → time list →
choice → patient → phone/name → confirmation), calls the backend only
through the injected `ToolExecutor` (AssistantOrchestrator in production)
and never executes a mutation without an explicit «да».
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.dialogue import templates as t
from app.dialogue.emergency import EMERGENCY_REPLY, is_emergency
from app.dialogue.state import (
    ClinicRef,
    DialogueState,
    DoctorRef,
    Flow,
    PendingAction,
    RecordOption,
    SlotOption,
    Stage,
)
from app.nlu.contract import CITIES, INFORMATIVE_INTENT
from app.nlu.engine import NluEngine
from app.nlu.fallbacks import (
    detect_patient_mode,
    detect_relative_date,
    detect_smalltalk,
    detect_specialty,
    detect_specialty_fuzzy,
    detect_yes_no,
    extract_full_name,
    extract_phone,
    is_cancel_go_ahead,
    name_in_text,
    phone_in_text,
    specialty_mentioned,
    wants_another_date,
)
from app.nlu.schemas import NluParse
from app.nlu.values import (
    format_hm,
    match_doctors,
    normalize_phone,
    phone_variants,
    resolve_date,
    to_local,
)
from app.services import geo as geo_service

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Clock = Callable[[], datetime]

# Dates the model read as a calendar day or a week: not a typo of «завтра».
_SPECIFIC_DATE_PREFIXES = ("next_week:", "2", "0", "1")
MAX_OPTIONS = 5
NEAREST_DAYS = 10
NEAREST_RESCANS = 4

_FLOW_BY_INTENT: dict[str, Flow] = {
    "book_appointment": Flow.BOOK,
    "find_nearest_slots": Flow.SLOTS,
    "find_slots": Flow.SLOTS,
    "find_doctors": Flow.DOCTORS,
    "find_clinics": Flow.CLINICS,
    "cancel_appointment": Flow.CANCEL,
    "reschedule_appointment": Flow.RESCHEDULE,
    "get_appointments": Flow.RECORDS,
    "get_appointment": Flow.RECORDS,
}
_SEARCH_KEYS = ("specialty", "date", "time", "time_after", "doctor")
#: Intents that always start (or refine) a search, even without slots.
_SEARCH_INTENTS = frozenset({"find_slots", "find_nearest_slots", "find_doctors", "find_clinics"})
_YES_NO_STAGES = frozenset(
    {Stage.CONFIRM, Stage.NO_SLOTS, Stage.OFFER_DOCTOR, Stage.OFFER_BOOK, Stage.PREVIEW}
)
_RETRY_CODES = frozenset(
    {"SLOT_ALREADY_BOOKED", "CANNOT_BOOK_IN_PAST", "CANNOT_RESCHEDULE_IN_PAST", "SAME_SLOT"}
)


def _details(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("details")
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _is_success(result: dict[str, Any]) -> bool:
    return result.get("status") == "success"


def _doctor_ref(raw: object) -> DoctorRef | None:
    if not isinstance(raw, dict):
        return None
    doctor_id, name = raw.get("id"), raw.get("full_name")
    if isinstance(doctor_id, int) and isinstance(name, str):
        return DoctorRef(doctor_id, name)
    return None


class DialogueManager:
    """Stateless between turns: everything lives in the DialogueState passed in."""

    def __init__(
        self,
        engine: NluEngine,
        execute: ToolExecutor,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._engine = engine
        self._execute = execute
        self._clock: Clock = clock or (lambda: datetime.now(UTC))

    # -- entry point -------------------------------------------------------

    async def handle(
        self, state: DialogueState, text: str, *, allow_defer: bool = False
    ) -> str | None:
        """Answer one user turn.

        Returns None only when `allow_defer` is set and nothing in the
        utterance could be understood: the caller may then hand the turn
        to another component (the rule-based pipeline). Mutating state is left untouched
        in that case.
        """
        if is_emergency(text):
            return self._say(state, EMERGENCY_REPLY)
        parse = await asyncio.to_thread(self._engine.parse, text, state.last_response)
        logger.info(
            "nlu intent=%s conf=%.3f slots=%s stage=%s",
            parse.intent,
            parse.confidence,
            sorted(parse.slots),
            state.stage,
        )
        state.last_parse = parse
        repeats_before = state.repeat_count
        reply = await self._dispatch(state, text, parse, allow_defer=allow_defer)
        if reply is None:
            return None
        if state.repeat_count == repeats_before:
            # A productive turn resets the repeat ladder (only _unmatched raises it).
            state.repeat_count = 0
        return self._say(state, reply)

    def _say(self, state: DialogueState, reply: str) -> str:
        state.last_response = reply
        state.turns += 1
        return reply

    # -- turn routing ------------------------------------------------------

    async def _dispatch(
        self, state: DialogueState, text: str, parse: NluParse, *, allow_defer: bool
    ) -> str | None:
        intent = parse.intent if parse.confident else INFORMATIVE_INTENT
        slots = self._with_fallbacks(state, text, parse.slots, intent)

        if wants_another_date(text) and state.stage in (Stage.NO_SLOTS, Stage.SELECT_SLOT):
            # «давайте другую дату» works while choosing too, not just on no-slots.
            state.nearest = False
            state.options = []
            state.wants.pop("date", None)
            state.stage = Stage.ASK_DATE
            return t.ask_date(state.turns)
        if intent in ("confirm", "reject") and (
            state.stage is Stage.ASK_PATIENT and "patient_mode" in slots
        ):
            # «нет» to «были ли вы у нас или впервые?» means «впервые», not a refusal.
            intent = INFORMATIVE_INTENT

        if state.stage in _YES_NO_STAGES:
            answer = self._yes_no(parse, text)
            if (
                state.pending is not None
                and state.pending.kind == "cancel"
                and is_cancel_go_ahead(text)
            ):
                answer = "yes"
            if answer is None and state.stage is Stage.PREVIEW and intent == "book_appointment":
                answer = "yes"
            if answer is not None:
                return await self._on_yes_no(state, answer)

        if state.stage is Stage.SELECT_SLOT and self._has_search_update(intent, slots):
            # A fresh search («а в Казани?», «12.10», «после двух») replaces the
            # offered list instead of being misread as a slot choice.
            flow = _FLOW_BY_INTENT.get(intent)
            if flow is not None:
                self._enter_flow(state, flow, nearest=intent == "find_nearest_slots")
            return await self._merge_and_advance(state, slots)
        if state.stage is Stage.SELECT_SLOT and self._is_selection(intent, slots):
            option = self._pick_slot(state, slots)
            if option is None:
                return t.repeat(state.last_response)
            return await self._after_slot_chosen(state, option)
        if state.stage is Stage.SELECT_RECORD and self._is_selection(intent, slots):
            record = self._pick_record(state, slots)
            if record is None:
                return t.repeat(state.last_response)
            return await self._after_record_chosen(state, record)

        flow = _FLOW_BY_INTENT.get(intent)
        if flow is not None:
            self._enter_flow(state, flow, nearest=intent == "find_nearest_slots")
            return await self._merge_and_advance(state, slots)
        if intent == "greeting":
            return t.GREET_REPLIES[state.turns % len(t.GREET_REPLIES)]
        if intent == "health_concern":
            self._enter_flow(state, Flow.BOOK)
            state.stage = Stage.ASK_SPECIALTY
            if "specialty" in slots:
                return await self._merge_and_advance(state, slots)
            return t.HEALTH
        if intent == "reject":
            state.reset_flow()
            return t.WORKFLOW_CANCELLED
        if intent in ("confirm", "select_option"):
            return self._unmatched(state, text, parse, allow_defer=False)

        return await self._informative(state, text, slots, parse, allow_defer=allow_defer)

    async def _informative(
        self,
        state: DialogueState,
        text: str,
        slots: dict[str, str],
        parse: NluParse,
        *,
        allow_defer: bool,
    ) -> str | None:
        """An answer carrying slots, or something unintelligible."""
        useful = {k: v for k, v in slots.items() if k in {*_SEARCH_KEYS, *_PROFILE_KEYS, "city"}}
        if state.flow is not Flow.NONE:
            if not useful:
                return self._unmatched(state, text, parse, allow_defer=False)
            return await self._merge_and_advance(state, slots)
        if "specialty" in useful or "doctor" in useful:
            self._enter_flow(state, Flow.BOOK)
            return await self._merge_and_advance(state, slots)
        if "city" in useful:
            self._enter_flow(state, Flow.CLINICS)
            return await self._merge_and_advance(state, slots)
        return self._unmatched(state, text, parse, allow_defer=allow_defer)

    def _unmatched(
        self, state: DialogueState, text: str, parse: NluParse, *, allow_defer: bool
    ) -> str | None:
        if state.stage is Stage.IDLE:
            smalltalk = detect_smalltalk(text)
            if smalltalk is not None:
                return {"thanks": t.thanks(state.turns), "bye": t.bye(state.turns)}.get(
                    smalltalk, t.FALLBACK
                )
        if allow_defer and state.stage is Stage.IDLE and not parse.confident:
            return None
        state.repeat_count += 1
        if state.stage is Stage.IDLE:
            return t.NOT_UNDERSTOOD
        return t.repeat_escalated(state.stage, state.last_response, state.repeat_count)

    @staticmethod
    def _has_search_update(intent: str, slots: dict[str, str]) -> bool:
        """True if the turn refines the search (not a slot choice)."""
        if intent in _SEARCH_INTENTS:
            return True
        return any(
            key in slots for key in ("specialty", "date", "time", "time_after", "doctor", "city")
        )

    @staticmethod
    def _is_selection(intent: str, slots: dict[str, str]) -> bool:
        return (
            intent in ("select_option", "book_appointment")
            or "selection" in slots
            or "selection_time" in slots
        )

    @staticmethod
    def _yes_no(parse: NluParse, text: str) -> str | None:
        if parse.confident and parse.intent == "confirm":
            return "yes"
        if parse.confident and parse.intent == "reject":
            return "no"
        if not parse.confident or parse.intent == INFORMATIVE_INTENT:
            return detect_yes_no(text)
        return None

    def _with_fallbacks(
        self, state: DialogueState, text: str, slots: dict[str, str], intent: str
    ) -> dict[str, str]:
        """Back the model up with deterministic extractors.

        Free-form values (phone, name) are verified against what the user
        actually typed: the seq2seq model sometimes drops a digit or invents
        a name, so a regex match wins and an unverifiable value is dropped.
        """
        merged = dict(slots)
        phone = extract_phone(text)
        if phone is not None:
            merged["phone"] = phone
        elif "phone" in merged and not phone_in_text(merged["phone"], text):
            del merged["phone"]
        if "full_name" in merged and not name_in_text(merged["full_name"], text):
            del merged["full_name"]
        # A typo («кордиологу») makes the seq2seq model guess a wrong specialty:
        # trust what is actually written (exactly or with a typo) over the model.
        named = detect_specialty(text) or detect_specialty_fuzzy(text)
        if named is not None:
            model_specialty = merged.get("specialty")
            if model_specialty is None or not specialty_mentioned(model_specialty, text):
                merged["specialty"] = named
        else:
            known = state.wants.get("specialty")
            if (
                intent == INFORMATIVE_INTENT
                and known is not None
                and merged.get("specialty", known) != known
            ):
                # A date or a city answer must not silently change the specialty.
                del merged["specialty"]
        relative_date = detect_relative_date(text)
        model_date = merged.get("date", relative_date or "")
        if relative_date is not None and not model_date.startswith(_SPECIFIC_DATE_PREFIXES):
            merged["date"] = relative_date
        if "city" in merged and not geo_service.city_mentioned(merged["city"], text):
            # A stale model may guess a city from its old training vocabulary
            # («warszawa» for «москва»): trust what is actually written.
            del merged["city"]
        if "city" not in merged:
            city = geo_service.canonical_city(text)
            if city in CITIES:
                merged["city"] = city
        asking_name = state.stage is Stage.ASK_NAME and "full_name" not in merged
        if asking_name and (name := extract_full_name(text)) is not None:
            merged["full_name"] = name
        asking_patient = state.stage is Stage.ASK_PATIENT and "patient_mode" not in merged
        if asking_patient and (mode := detect_patient_mode(text)) is not None:
            merged["patient_mode"] = mode
        return merged

    # -- yes / no answers --------------------------------------------------

    async def _on_yes_no(self, state: DialogueState, answer: str) -> str:
        stage = state.stage
        if answer == "no":
            state.reset_flow()
            return t.WORKFLOW_CANCELLED
        if stage is Stage.CONFIRM:
            return await self._execute_pending(state)
        if stage is Stage.NO_SLOTS:
            state.nearest = True
            return await self._search_and_offer(state)
        if stage is Stage.OFFER_DOCTOR:
            state.flow = Flow.BOOK
            state.stage = Stage.IDLE
            return await self._advance(state)
        if stage is Stage.OFFER_BOOK:
            self._enter_flow(state, Flow.BOOK)
            return await self._advance(state)
        # PREVIEW: the user wants to book the previewed slot.
        state.flow = Flow.BOOK
        return await self._advance(state)

    # -- flow bookkeeping --------------------------------------------------

    def _enter_flow(self, state: DialogueState, flow: Flow, *, nearest: bool = False) -> None:
        if state.flow is not flow:
            # Search-like flows share one context (specialty/doctor/date/city):
            # switching between them refines the search instead of starting over.
            # «ближайшее окно» after «кардиолог» must keep the specialty.
            keep = state.flow in (Flow.BOOK, Flow.SLOTS, Flow.DOCTORS) and flow in (
                Flow.BOOK,
                Flow.SLOTS,
                Flow.DOCTORS,
            )
            stashed_wants = dict(state.wants) if keep else None
            stashed_doctor = state.doctor if keep else None
            state.reset_flow()
            state.flow = flow
            if stashed_wants is not None:
                state.wants = stashed_wants
                state.doctor = stashed_doctor
        state.nearest = state.nearest or nearest
        state.pending = None

    async def _merge_and_advance(self, state: DialogueState, slots: dict[str, str]) -> str:
        error = await self._merge(state, slots)
        if error is not None:
            return error
        return await self._advance(state)

    async def _merge(self, state: DialogueState, slots: dict[str, str]) -> str | None:
        """Fold slots into the state. Returns a reply only if the city cannot be served."""
        for key in _SEARCH_KEYS:
            if key in slots:
                state.wants[key] = slots[key]
        if "specialty" in slots and "doctor" not in slots:
            state.doctor = None
            state.wants.pop("doctor", None)
        if "doctor" in slots:
            state.doctor = None
        if "patient_mode" in slots:
            state.patient_mode = slots["patient_mode"]
        if "phone" in slots and state.patient_id is None:
            state.patient_phone = slots["phone"]
            if state.patient_mode is None and state.stage is Stage.ASK_PATIENT:
                state.patient_mode = "registered"
        if "full_name" in slots and state.patient_id is None:
            state.patient_name = slots["full_name"]
        if "city" in slots and slots["city"] != state.city:
            clinic = await self._find_clinic(slots["city"])
            if clinic is None:
                return t.CITY_NOT_SERVED
            if state.clinic is not None and clinic.id != state.clinic.id:
                state.patient_id = None
                state.chosen_slot = None
                state.options = []
                state.all_options = []
            state.clinic = clinic
            state.city = slots["city"]
        return None

    async def _find_clinic(self, city: str) -> ClinicRef | None:
        result = await self._execute("find_clinics", {"city": city})
        for raw in _dicts(_details(result).get("clinics")):
            clinic_id, name, tz = raw.get("id"), raw.get("name"), raw.get("timezone")
            clinic_city = raw.get("city")
            if (
                isinstance(clinic_id, int)
                and isinstance(name, str)
                and isinstance(tz, str)
                and isinstance(clinic_city, str)
                and geo_service.canonical_city(clinic_city) == city
            ):
                return ClinicRef(clinic_id, name, tz)
        return None

    async def _advance(self, state: DialogueState) -> str:
        flow = state.flow
        if flow is Flow.BOOK and state.chosen_slot is not None:
            return await self._advance_patient(state)
        if flow in (Flow.BOOK, Flow.SLOTS):
            return await self._advance_search(state)
        if flow is Flow.RESCHEDULE:
            if state.chosen_record is None:
                return await self._advance_records(state)
            return await self._advance_search(state)
        if flow is Flow.DOCTORS:
            return await self._advance_doctors(state)
        if flow is Flow.CLINICS:
            return await self._advance_clinics(state)
        if flow in (Flow.CANCEL, Flow.RECORDS):
            return await self._advance_records(state)
        return t.NOT_UNDERSTOOD

    # -- clinics -----------------------------------------------------------

    async def _advance_clinics(self, state: DialogueState) -> str:
        result = await self._execute("find_clinics", {"city": state.city} if state.city else {})
        rows: list[tuple[str, str | None]] = []
        for raw in _dicts(_details(result).get("clinics")):
            name, address, city = raw.get("name"), raw.get("address"), raw.get("city")
            if not isinstance(name, str) or not isinstance(city, str):
                continue
            if state.city is None or geo_service.canonical_city(city) == state.city:
                rows.append((name, address if isinstance(address, str) else None))
        state.reset_flow()
        return t.clinics_reply(rows) if rows else t.CITY_NOT_SERVED

    # -- doctors -----------------------------------------------------------

    async def _advance_doctors(self, state: DialogueState) -> str:
        wants = state.wants
        if "specialty" not in wants and "doctor" not in wants:
            state.stage = Stage.ASK_SPECIALTY
            return t.ASK_SPEC
        if state.clinic is None:
            state.stage = Stage.ASK_CITY
            return t.ask_city_spec(wants.get("specialty"), state.turns)
        args: dict[str, Any] = {"clinic_id": state.clinic.id}
        if "specialty" in wants:
            args["specialty_name"] = wants["specialty"]
        result = await self._execute("find_doctors", args)
        if result.get("code") == "SPECIALTY_NOT_FOUND":
            return self._specialty_unavailable(state)
        doctors = [
            ref
            for raw in _dicts(_details(result).get("doctors"))
            if (ref := _doctor_ref(raw)) is not None
        ]
        surname = wants.get("doctor")
        if surname is not None:
            doctors = [doctors[i] for i in match_doctors(surname, [d.name for d in doctors])]
        if not doctors:
            state.wants.pop("doctor", None)
            state.stage = Stage.ASK_SPECIALTY
            return t.NO_DOCTORS
        state.stage = Stage.OFFER_DOCTOR
        if len(doctors) == 1:
            state.doctor = doctors[0]
            return t.doctors_found(doctors[0].name)
        names = ", ".join(d.name for d in doctors)
        return f"Нашла врачей: {names}. Хотите записаться?"

    def _specialty_unavailable(self, state: DialogueState) -> str:
        specialty = state.wants.get("specialty", "")
        state.clinic = None
        state.city = None
        state.stage = Stage.ASK_CITY
        return t.spec_na(specialty)

    # -- search: specialty → date → city → slots ---------------------------

    async def _advance_search(self, state: DialogueState) -> str:
        wants = state.wants
        has_target = state.doctor is not None or "specialty" in wants or "doctor" in wants
        if not has_target:
            state.stage = Stage.ASK_SPECIALTY
            return t.ASK_SPEC
        if not state.nearest and "date" not in wants:
            state.stage = Stage.ASK_DATE
            return t.ask_date(state.turns)
        if state.clinic is None:
            state.stage = Stage.ASK_CITY
            return t.ask_city_spec(wants.get("specialty"), state.turns)
        return await self._search_and_offer(state)

    def _today(self, clinic: ClinicRef) -> date:
        return self._clock().astimezone(ZoneInfo(clinic.timezone)).date()

    async def _search_target(self, state: DialogueState, clinic: ClinicRef) -> dict[str, Any] | str:
        """Backend arguments that identify who to look slots for, or an error reply."""
        wants = state.wants
        if state.doctor is None and "doctor" in wants:
            result = await self._execute("find_doctors", {"clinic_id": clinic.id})
            doctors = [
                ref
                for raw in _dicts(_details(result).get("doctors"))
                if (ref := _doctor_ref(raw)) is not None
            ]
            found = match_doctors(wants["doctor"], [d.name for d in doctors])
            if not found:
                wants.pop("doctor", None)
                state.stage = Stage.ASK_SPECIALTY
                return t.NO_DOCTORS
            state.doctor = doctors[found[0]]
        if state.doctor is not None:
            return {"doctor_id": state.doctor.id}
        return {"specialty_name": wants["specialty"]}

    async def _search_and_offer(self, state: DialogueState) -> str:
        clinic = state.clinic
        if clinic is None:
            state.stage = Stage.ASK_CITY
            return t.ask_city_spec(state.wants.get("specialty"), state.turns)
        target = await self._search_target(state, clinic)
        if isinstance(target, str):
            return target
        today = self._today(clinic)
        raw_date = state.wants.get("date")
        day = resolve_date(raw_date, today) if raw_date else None
        if day is not None and day < today:
            state.wants.pop("date", None)
            state.stage = Stage.ASK_DATE
            return t.PAST_DATE
        if state.nearest or day is None:
            return await self._offer_nearest(state, clinic, target, day or today + timedelta(1))
        result = await self._execute(
            "find_slots", {"clinic_id": clinic.id, "date": day.isoformat(), **target}
        )
        failure = self._search_failure(state, result)
        if failure is not None:
            return failure
        options = self._filter(state, self._options(result, clinic))
        if not options:
            # No extra turn: offer the nearest windows right away. The user can
            # still pick another date while choosing (wants_another_date).
            state.nearest = True
            return await self._offer_nearest(
                state, clinic, target, day + timedelta(days=1), prefix=t.NO_SLOTS_AUTO
            )
        reply = self._offer(state, options)
        wanted = state.wants.get("time")
        if wanted and len(options) == 1 and options[0].hm == wanted:
            # «на 14:00» matched exactly one slot: the user already chose it.
            return await self._after_slot_chosen(state, options[0])
        return reply

    async def _offer_nearest(
        self,
        state: DialogueState,
        clinic: ClinicRef,
        target: dict[str, Any],
        start: date,
        prefix: str = "",
    ) -> str:
        for _ in range(NEAREST_RESCANS):
            result = await self._execute(
                "find_nearest_slots",
                {
                    "clinic_id": clinic.id,
                    "date": start.isoformat(),
                    "days_ahead": NEAREST_DAYS,
                    **target,
                },
            )
            failure = self._search_failure(state, result)
            if failure is not None:
                return failure
            raw_options = self._options(result, clinic)
            if not raw_options:
                break
            options = self._filter(state, raw_options)
            if options:
                return self._offer(state, options, prefix)
            # The earliest day has nothing matching the time wish: look further.
            start = raw_options[-1].day + timedelta(days=1)
        state.nearest = False
        state.wants.pop("date", None)
        state.stage = Stage.ASK_DATE
        return t.NO_SLOTS_NEAR

    def _search_failure(self, state: DialogueState, result: dict[str, Any]) -> str | None:
        """Reply for a failed search, None if the result holds slots (or just no slots)."""
        if _is_success(result) or result.get("code") == "NO_SLOTS_AVAILABLE":
            return None
        code = result.get("code")
        if code in ("SPECIALTY_NOT_FOUND", "NO_DOCTORS_FOR_SPECIALTY"):
            return self._specialty_unavailable(state)
        if code in ("DOCTOR_NOT_FOUND", "AMBIGUOUS_DOCTOR"):
            state.wants.pop("doctor", None)
            state.doctor = None
            state.stage = Stage.ASK_SPECIALTY
            return t.NO_DOCTORS
        logger.warning("slot search failed: %s", code)
        state.reset_flow()
        return t.ERROR

    def _options(self, result: dict[str, Any], clinic: ClinicRef) -> list[SlotOption]:
        options: dict[str, SlotOption] = {}
        for raw in _dicts(_details(result).get("slots")):
            starts_at, doctor = raw.get("starts_at"), _doctor_ref(raw.get("doctor"))
            if not isinstance(starts_at, str) or doctor is None:
                continue
            local = to_local(starts_at, clinic.timezone)
            options.setdefault(
                starts_at, SlotOption(starts_at, local.date(), format_hm(local), doctor)
            )
        return sorted(options.values(), key=lambda o: (o.day, o.hm, o.doctor.id))

    @staticmethod
    def _filter(state: DialogueState, options: list[SlotOption]) -> list[SlotOption]:
        """Apply «в 15:00» / «после двух» wishes to the day's slots."""
        wanted = state.wants.get("time")
        if wanted:
            exact = [o for o in options if o.hm == wanted]
            options = exact or [o for o in options if o.hm >= wanted]
        after = state.wants.get("time_after")
        if after:
            options = [o for o in options if o.hm >= after]
        return options

    def _offer(self, state: DialogueState, options: list[SlotOption], prefix: str = "") -> str:
        state.all_options = options
        state.options = options[:MAX_OPTIONS]
        state.stage = Stage.SELECT_SLOT
        return prefix + t.slots_reply([(o.day, o.hm) for o in state.options])

    # -- choosing a slot / a record ---------------------------------------

    @staticmethod
    def _pick_slot(state: DialogueState, slots: dict[str, str]) -> SlotOption | None:
        wanted_time = slots.get("selection_time")
        if wanted_time:
            return next((o for o in state.all_options if o.hm == wanted_time), None)
        return _pick(state.options, slots.get("selection"))

    @staticmethod
    def _pick_record(state: DialogueState, slots: dict[str, str]) -> RecordOption | None:
        return _pick(state.records, slots.get("selection"))

    async def _after_slot_chosen(self, state: DialogueState, option: SlotOption) -> str:
        state.chosen_slot = option
        if state.flow is Flow.SLOTS:
            state.stage = Stage.PREVIEW
            return t.preview_reply(option.day, option.hm)
        if state.flow is Flow.RESCHEDULE:
            return await self._prepare_reschedule(state)
        return await self._advance_patient(state)

    async def _after_record_chosen(self, state: DialogueState, record: RecordOption) -> str:
        state.chosen_record = record
        if state.flow is Flow.CANCEL:
            return await self._prepare_cancel(state)
        state.doctor = record.doctor
        state.wants.pop("specialty", None)
        return await self._advance_search(state)

    # -- patient identification and booking --------------------------------

    async def _identify_patient(self, state: DialogueState, *, allow_new: bool) -> str | None:
        """Ask for what is still missing to know the patient; None when known."""
        if state.patient_id is not None:
            return None
        mode = state.patient_mode
        if mode is None and not allow_new:
            # Only a registered patient can have appointments: skip the question.
            mode = state.patient_mode = "registered"
        if mode is None:
            state.stage = Stage.ASK_PATIENT
            return t.ASK_PATIENT
        if mode == "registered":
            if not state.patient_phone:
                state.stage = Stage.ASK_PHONE
                return t.ASK_PHONE
            if await self._lookup_patient(state):
                return None
            state.patient_mode = None
            state.patient_phone = None
            state.stage = Stage.ASK_PATIENT
            return t.PATIENT_NOT_FOUND
        if not allow_new:
            state.reset_flow()
            state.stage = Stage.OFFER_BOOK
            return t.NO_RECORDS
        if not state.patient_name:
            state.stage = Stage.ASK_NAME
            return t.ASK_NAME
        if not state.patient_phone:
            state.stage = Stage.ASK_PHONE
            return t.ASK_PHONE
        return None

    async def _lookup_patient(self, state: DialogueState) -> bool:
        if state.clinic is None or not state.patient_phone:
            return False
        for phone in phone_variants(state.patient_phone, state.city):
            result = await self._execute(
                "find_patient", {"clinic_id": state.clinic.id, "phone": phone}
            )
            patient = _details(result).get("patient")
            if _is_success(result) and isinstance(patient, dict):
                patient_id, name = patient.get("id"), patient.get("full_name")
                if isinstance(patient_id, int):
                    state.patient_id = patient_id
                    state.patient_name = name if isinstance(name, str) else state.patient_name
                    return True
        return False

    async def _advance_patient(self, state: DialogueState) -> str:
        slot, clinic = state.chosen_slot, state.clinic
        if slot is None or clinic is None:
            state.reset_flow()
            return t.ERROR
        missing = await self._identify_patient(state, allow_new=True)
        if missing is not None:
            return missing
        args: dict[str, Any] = {
            "clinic_id": clinic.id,
            "doctor_id": slot.doctor.id,
            "starts_at": slot.starts_at,
        }
        new_name: str | None = None
        if state.patient_id is not None:
            args["patient_id"] = state.patient_id
            preview = await self._execute("book_appointment", {**args, "confirmed": False})
            if preview.get("code") != "CONFIRM_BOOKING":
                return await self._booking_failed(state, preview)
        else:
            args["full_name"] = state.patient_name
            args["phone"] = normalize_phone(state.patient_phone or "", state.city)
            new_name = state.patient_name
        state.pending = PendingAction(
            "book",
            "book_appointment",
            {**args, "confirmed": True},
            f" Врач: {slot.doctor.name}, клиника «{clinic.name}».",
        )
        state.stage = Stage.CONFIRM
        return t.confirm_book(slot.day, slot.hm, new_name) + state.pending.details

    async def _booking_failed(self, state: DialogueState, result: dict[str, Any]) -> str:
        if result.get("code") in _RETRY_CODES:
            state.chosen_slot = None
            state.pending = None
            return t.SLOT_TAKEN + await self._search_and_offer(state)
        logger.warning("booking preview failed: %s", result.get("code"))
        state.reset_flow()
        return t.ERROR

    # -- appointments: list / cancel / reschedule --------------------------

    async def _advance_records(self, state: DialogueState) -> str:
        clinic = state.clinic
        if clinic is None:
            state.stage = Stage.ASK_CITY
            return t.ask_city(state.turns)
        missing = await self._identify_patient(state, allow_new=False)
        if missing is not None:
            return missing
        args: dict[str, Any] = {"clinic_id": clinic.id, "patient_id": state.patient_id}
        if state.flow in (Flow.CANCEL, Flow.RESCHEDULE):
            args["appointment_status"] = "booked"
        result = await self._execute("get_appointments", args)
        records = self._records(result, clinic)
        if not records:
            state.reset_flow()
            state.stage = Stage.OFFER_BOOK
            return t.NO_RECORDS
        state.records = records
        listing = t.records_reply([(r.day, r.hm, r.status, r.doctor.name) for r in records])
        if state.flow is Flow.RECORDS:
            state.reset_flow()
            return listing
        if len(records) == 1:
            return await self._after_record_chosen(state, records[0])
        state.stage = Stage.SELECT_RECORD
        return f"{listing} {t.SELECT_RECORD}"

    @staticmethod
    def _records(result: dict[str, Any], clinic: ClinicRef) -> list[RecordOption]:
        records: list[RecordOption] = []
        for raw in _dicts(_details(result).get("appointments")):
            appointment_id, starts_at = raw.get("id"), raw.get("starts_at")
            status, doctor = raw.get("status"), _doctor_ref(raw.get("doctor"))
            if (
                isinstance(appointment_id, int)
                and isinstance(starts_at, str)
                and isinstance(status, str)
                and doctor is not None
            ):
                local = to_local(starts_at, clinic.timezone)
                records.append(
                    RecordOption(
                        appointment_id, starts_at, local.date(), format_hm(local), status, doctor
                    )
                )
        return sorted(records, key=lambda r: r.starts_at)

    async def _prepare_cancel(self, state: DialogueState) -> str:
        record, clinic = state.chosen_record, state.clinic
        if record is None or clinic is None:
            state.reset_flow()
            return t.ERROR
        args: dict[str, Any] = {"clinic_id": clinic.id, "appointment_id": record.appointment_id}
        preview = await self._execute("cancel_appointment", {**args, "confirmed": False})
        if preview.get("code") != "CONFIRM_CANCEL":
            logger.warning("cancel preview failed: %s", preview.get("code"))
            state.reset_flow()
            return t.ERROR
        state.pending = PendingAction(
            "cancel",
            "cancel_appointment",
            {**args, "confirmed": True},
            f" Врач: {record.doctor.name}, клиника «{clinic.name}».",
        )
        state.stage = Stage.CONFIRM
        return t.confirm_cancel(record.day, record.hm) + state.pending.details

    async def _prepare_reschedule(self, state: DialogueState) -> str:
        record, slot, clinic = state.chosen_record, state.chosen_slot, state.clinic
        if record is None or slot is None or clinic is None:
            state.reset_flow()
            return t.ERROR
        args: dict[str, Any] = {
            "clinic_id": clinic.id,
            "appointment_id": record.appointment_id,
            "new_starts_at": slot.starts_at,
        }
        preview = await self._execute("reschedule_appointment", {**args, "confirmed": False})
        if preview.get("code") != "CONFIRM_RESCHEDULE":
            return await self._booking_failed(state, preview)
        state.pending = PendingAction(
            "reschedule",
            "reschedule_appointment",
            {**args, "confirmed": True},
            f" Врач: {record.doctor.name}, клиника «{clinic.name}».",
        )
        state.stage = Stage.CONFIRM
        return t.confirm_reschedule(slot.day, slot.hm) + state.pending.details

    async def _execute_pending(self, state: DialogueState) -> str:
        pending = state.pending
        if pending is None:
            state.reset_flow()
            return t.ERROR
        result = await self._execute(pending.tool, dict(pending.args))
        slot, record = state.chosen_slot, state.chosen_record
        if _is_success(result):
            reply = self._success_reply(state, pending, result, slot, record)
            state.reset_flow()
            return reply
        if pending.kind != "cancel" and result.get("code") in _RETRY_CODES:
            state.pending = None
            state.chosen_slot = None
            return t.SLOT_TAKEN + await self._search_and_offer(state)
        logger.warning("%s failed: %s", pending.tool, result.get("code"))
        state.reset_flow()
        return t.ERROR

    @staticmethod
    def _success_reply(
        state: DialogueState,
        pending: PendingAction,
        result: dict[str, Any],
        slot: SlotOption | None,
        record: RecordOption | None,
    ) -> str:
        if pending.kind == "cancel":
            return f"{t.CANCELLED} {t.cancelled_tail(state.turns)}"
        suffix = ""
        if slot is not None:
            suffix = f" {slot.doctor.name}, {t.dm(slot.day)} в {slot.hm}."
        if pending.kind == "reschedule":
            return f"{t.RESCHEDULED}{suffix} {t.rescheduled_tail(state.turns)}"
        patient = _details(result).get("appointment", {}).get("patient")
        if isinstance(patient, dict) and isinstance(patient.get("id"), int):
            state.patient_id = patient["id"]
        return f"{t.BOOKED}{suffix} {t.booked_tail(state.turns)}"


_PROFILE_KEYS: Iterable[str] = ("patient_mode", "phone", "full_name")


def _pick[T](options: list[T], selection: str | None) -> T | None:
    """Option by «1»…«6» / «earliest» / «latest» (index into the shown list)."""
    if not options or selection is None:
        return None
    if selection == "earliest":
        return options[0]
    if selection == "latest":
        return options[-1]
    if selection.isdigit() and 1 <= int(selection) <= len(options):
        return options[int(selection) - 1]
    return None
