"""Per-conversation dialogue state (in-process memory)."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from app.nlu.schemas import NluParse


class Flow(StrEnum):
    """What the user is trying to do."""

    NONE = "none"
    BOOK = "book"  # find a time and book it
    SLOTS = "slots"  # only look for free time (find_slots / find_nearest_slots)
    DOCTORS = "doctors"
    CLINICS = "clinics"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    RECORDS = "records"  # show the patient's appointments


class Stage(StrEnum):
    """What the assistant is waiting for. Doubles as the NLU context."""

    IDLE = "idle"
    ASK_SPECIALTY = "ask_specialty"
    ASK_DATE = "ask_date"
    ASK_CITY = "ask_city"
    ASK_PATIENT = "ask_patient"
    ASK_PHONE = "ask_phone"
    ASK_NAME = "ask_name"
    SELECT_SLOT = "select_slot"
    SELECT_RECORD = "select_record"
    CONFIRM = "confirm"  # pending booking / cancellation / reschedule
    NO_SLOTS = "no_slots"  # «check nearest windows or pick another date?»
    OFFER_DOCTOR = "offer_doctor"  # «found doctor X, book with him?»
    OFFER_BOOK = "offer_book"  # «no appointments yet, book one?»
    PREVIEW = "preview"  # slot chosen in a search-only flow, waiting for «запишите»


@dataclass(slots=True)
class ClinicRef:
    id: int
    name: str
    timezone: str


@dataclass(slots=True)
class DoctorRef:
    id: int
    name: str


@dataclass(slots=True)
class SlotOption:
    starts_at: str  # ISO instant exactly as the backend returned it
    day: date  # clinic-local
    hm: str  # clinic-local HH:MM
    doctor: DoctorRef


@dataclass(slots=True)
class RecordOption:
    appointment_id: int
    starts_at: str
    day: date
    hm: str
    status: str
    doctor: DoctorRef


@dataclass(slots=True)
class PendingAction:
    """A mutating tool call waiting for the user's «да»."""

    kind: Literal["book", "cancel", "reschedule"]
    tool: str
    args: dict[str, Any]
    details: str  # human suffix appended after the template question


@dataclass(slots=True)
class DialogueState:
    flow: Flow = Flow.NONE
    stage: Stage = Stage.IDLE
    last_response: str = ""
    turns: int = 0
    # Search wishes collected from the user: specialty, date, time, time_after, doctor.
    wants: dict[str, str] = field(default_factory=dict)
    nearest: bool = False
    # Sticky between flows of one conversation.
    city: str | None = None
    clinic: ClinicRef | None = None
    patient_id: int | None = None
    patient_name: str | None = None
    patient_phone: str | None = None
    # Per-flow data.
    patient_mode: str | None = None
    doctor: DoctorRef | None = None
    options: list[SlotOption] = field(default_factory=list)
    all_options: list[SlotOption] = field(default_factory=list)
    records: list[RecordOption] = field(default_factory=list)
    chosen_slot: SlotOption | None = None
    chosen_record: RecordOption | None = None
    pending: PendingAction | None = None
    # What the NLU made of the latest utterance (read by the service for logging).
    last_parse: NluParse | None = None
    # Consecutive misunderstood turns (escalating repeats); reset by progress.
    repeat_count: int = 0

    def reset_flow(self) -> None:
        """Forget the current task, keep what identifies the user (clinic, patient)."""
        self.flow = Flow.NONE
        self.stage = Stage.IDLE
        self.wants = {}
        self.nearest = False
        self.patient_mode = None
        self.doctor = None
        self.options = []
        self.all_options = []
        self.records = []
        self.chosen_slot = None
        self.chosen_record = None
        self.pending = None
