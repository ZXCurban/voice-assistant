"""Typed contracts between the NLU/dialogue layer and the backend.

The parser/normalizer produces AssistantRequest (intent + normalized entities).
The backend answers with AssistantResult. No natural language is parsed here.
"""

from datetime import date as date_type
from datetime import time
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

AssistantIntent = Literal[
    "find_clinics",
    "find_specialties",
    "find_doctors",
    "get_doctor",
    "find_slots",
    "find_nearest_slots",
    "find_patient",
    "create_patient",
    "get_patient",
    "book_appointment",
    "get_appointment",
    "get_appointments",
    "reschedule_appointment",
    "cancel_appointment",
    "complete_appointment",
]

AssistantStatus = Literal[
    "success",
    "need_clarification",
    "not_found",
    "conflict",
    "invalid_input",
    "confirmation_required",
]


class AssistantContext(BaseModel):
    """Minimal conversational memory, managed by the dialogue layer.

    No persistence, no Redis, no history. Explicit request fields always
    win over context values.
    """

    model_config = ConfigDict(from_attributes=True)

    clinic_id: int | None = Field(default=None, gt=0)
    patient_id: int | None = Field(default=None, gt=0)
    selected_specialty_id: int | None = Field(default=None, gt=0)
    selected_doctor_id: int | None = Field(default=None, gt=0)
    selected_slot: AwareDatetime | None = None
    # Last user-mentioned city/address query (for find_clinics ranking).
    # The dialogue layer retains the latest resolved city between turns.
    city: str | None = Field(default=None, max_length=200)


class AssistantRequest(BaseModel):
    """Structured intent produced by the parser and normalizer."""

    model_config = ConfigDict(from_attributes=True)

    intent: AssistantIntent
    clinic_id: int | None = Field(default=None, gt=0)
    # Free-form city/address query for find_clinics ranking ("nearest clinic").
    # Optional: absent → original order. Validated like the rest.
    city: str | None = Field(default=None, min_length=2, max_length=200)
    address: str | None = Field(default=None, min_length=2, max_length=500)
    patient_id: int | None = Field(default=None, gt=0)
    specialty_id: int | None = Field(default=None, gt=0)
    specialty_name: str | None = Field(default=None, min_length=2, max_length=150)
    doctor_id: int | None = Field(default=None, gt=0)
    doctor_name: str | None = Field(default=None, min_length=2, max_length=200)
    appointment_id: int | None = Field(default=None, gt=0)
    # Clinic-local day for slot search (YYYY-MM-DD in clinic timezone).
    date: date_type | None = None
    # Optional clinic-local lower bound for slot searches from spoken phrases.
    time_after: time | None = None
    # Optional exact clinic-local time requested by the patient.
    time_at: time | None = None
    # How many days ahead to scan in find_nearest_slots (default 10, max 30).
    days_ahead: int | None = Field(default=None, ge=1, le=30)
    # Booking / reschedule target instant (must be tz-aware UTC).
    starts_at: AwareDatetime | None = None
    new_starts_at: AwareDatetime | None = None
    appointment_status: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=300)
    # create_patient fields.
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, min_length=5, max_length=50)
    # Dialogue layer sets this after the user confirms a mutating action.
    confirmed: bool = False
    context: AssistantContext | None = None


class AssistantResult(BaseModel):
    """Structured answer for the dialogue layer to verbalize.

    `details` carries machine-readable payloads (slots, appointments,
    candidates, previews) serialized as plain JSON-compatible data.
    """

    model_config = ConfigDict(from_attributes=True)

    status: AssistantStatus
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    requires_confirmation: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
