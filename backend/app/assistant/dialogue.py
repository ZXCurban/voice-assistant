"""In-process state for a multi-turn text assistant conversation."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DialoguePhase = Literal[
    "START",
    "COLLECTING_DATA",
    "SEARCHING",
    "SELECTING",
    "CONFIRMING",
    "EXECUTING",
    "COMPLETED",
    "CANCELLED",
    "ERROR",
    "WAITING_CLARIFICATION",
]

STATE_TTL = timedelta(minutes=30)


class DialogueState(BaseModel):
    """Only canonical fields and backend candidates are retained between turns."""

    model_config = ConfigDict(validate_assignment=True)

    conversation_id: str
    principal_id: str | None = None
    tenant_locked: bool = False
    phase: DialoguePhase = "START"
    intent: str | None = None
    awaiting_input: str | None = None
    pending_question: str | None = None
    patient_id: int | None = None
    patient_name: str | None = None
    patient_full_name: str | None = None
    patient_phone: str | None = None
    patient_mode: Literal["registered", "new"] | None = None
    specialty_id: int | None = None
    specialty_name: str | None = None
    clinic_id: int | None = None
    clinic_city: str | None = None
    clinic_timezone: str | None = None
    nearest_requested: bool = False
    patient_lookup_failed: bool = False
    doctor_id: int | None = None
    doctor_name: str | None = None
    date: date_type | None = None
    time_after: time | None = None
    time_at: time | None = None
    available_slots: list[dict[str, Any]] = Field(default_factory=list)
    selected_slot: dict[str, Any] | None = None
    appointment_candidates: list[dict[str, Any]] = Field(default_factory=list)
    selection_kind: Literal["slot", "appointment", "doctor", "clinic"] | None = None
    appointment_id: int | None = None
    pending_action: dict[str, Any] | None = None
    confirmation_state: Literal["none", "pending", "confirmed", "rejected"] = "none"
    workflow_step: str | None = None
    last_result: dict[str, Any] | None = None
    last_user_message_hash: str | None = None
    last_response: str | None = None
    last_activity: datetime = Field(default_factory=lambda: datetime.now(UTC))
    operation_key: str | None = None

    def touch(self) -> None:
        """Refresh expiry after any accepted user turn."""
        self.last_activity = datetime.now(UTC)

    def expired(self) -> bool:
        return datetime.now(UTC) - self.last_activity > STATE_TTL

    def clear_workflow(self) -> None:
        """Clear action state while retaining clinic and known patient identity."""
        self.phase = "START"
        self.intent = None
        self.awaiting_input = None
        self.pending_question = None
        self.specialty_id = None
        self.specialty_name = None
        self.doctor_id = None
        self.doctor_name = None
        self.date = None
        self.time_after = None
        self.time_at = None
        self.nearest_requested = False
        self.patient_lookup_failed = False
        self.patient_mode = None
        self.available_slots.clear()
        self.selected_slot = None
        self.appointment_candidates.clear()
        self.selection_kind = None
        self.appointment_id = None
        self.pending_action = None
        self.confirmation_state = "none"
        self.workflow_step = None
        self.operation_key = None
        self.last_result = None
        self.last_user_message_hash = None
        self.last_response = None
