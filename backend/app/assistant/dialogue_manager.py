"""Stateful deterministic text-dialogue workflows over AssistantOrchestrator."""

from __future__ import annotations

import re
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.dialogue import DialogueState
from app.assistant.nlu import NluParse, parse_utterance
from app.assistant.normalizer import normalize
from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.response import render_event
from app.assistant.schemas import AssistantRequest, AssistantResult


class DialogueManager:
    """Owns conversational state; domain mutations remain in backend services."""

    def __init__(self, orchestrator: AssistantOrchestrator) -> None:
        self.orchestrator = orchestrator

    async def handle(self, session: AsyncSession, state: DialogueState, message: str) -> str:
        if state.expired():
            state.clear_workflow()
        message_hash = sha256(message.encode()).hexdigest()
        if state.last_user_message_hash == message_hash and state.last_response:
            state.touch()
            return state.last_response

        parsed = parse_utterance(message)
        normalized = normalize(parsed, clinic_id=state.clinic_id)
        slots = {name: item.value for name, item in parsed.slots.items() if item.confidence >= 0.75}

        if state.awaiting_input == "full_name" and "full_name" not in slots:
            full_name = self._extract_contextual_name(message)
            if full_name:
                slots["full_name"] = full_name

        if state.awaiting_input == "patient_mode" and "patient_mode" not in slots:
            patient_mode = self._extract_patient_mode(message)
            if patient_mode is not None:
                slots["patient_mode"] = patient_mode
                if patient_mode == "new":
                    full_name = self._extract_contextual_name(message)
                    if full_name:
                        slots["full_name"] = full_name
        if (
            slots.get("patient_mode") == "new"
            and "full_name" not in slots
            and (state.awaiting_input == "patient_mode" or state.awaiting_input == "full_name")
        ):
            full_name = self._extract_contextual_name(message)
            if full_name:
                slots["full_name"] = full_name

        if parsed.intent == "health_concern" and state.intent is None:
            state.intent = "book_appointment"
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(state, message, "health_concern", {})

        if parsed.intent == "greeting":
            if state.intent is not None and state.last_response:
                return self._remember(
                    state,
                    message,
                    "continue_dialogue",
                    {"prompt": state.last_response},
                )
            return self._remember(state, message, "greeting", {})

        if parsed.intent == "reject" or re.fullmatch(
            r"\s*(?:отмена|стоп)\s*[!.]?\s*", message, re.I
        ):
            state.clear_workflow()
            return self._remember(state, message, "workflow_cancelled", {})

        if parsed.intent == "confirm":
            if state.awaiting_input == "doctor_booking" and state.doctor_id is not None:
                state.intent = "book_appointment"
                state.phase = "COLLECTING_DATA"
                state.awaiting_input = None
                parsed = NluParse(intent="book_appointment", confidence=0.99, slots=parsed.slots)
            elif state.awaiting_input == "date_or_nearest":
                state.nearest_requested = True
                state.date = None
                state.available_slots.clear()
                state.selected_slot = None
                state.phase = "COLLECTING_DATA"
                parsed = NluParse(intent="find_nearest_slots", confidence=0.99, slots=parsed.slots)
            else:
                if state.phase == "COMPLETED" and state.last_result:
                    return self._remember(state, message, "already_completed", state.last_result)
                if state.phase == "CONFIRMING" and state.pending_action:
                    action = state.pending_action
                    action["confirmed"] = True
                    state.phase = "EXECUTING"
                    state.confirmation_state = "confirmed"
                    result = await self._call(session, action)
                    if result.status == "success":
                        state.last_result = result.model_dump(mode="json")
                        state.phase = "COMPLETED"
                        state.pending_action = None
                        return self._remember(
                            state, message, self._event_for(result), result.details
                        )
                    state.phase = "ERROR"
                    state.confirmation_state = "pending"
                    return self._remember(state, message, "backend_error", {})
                return self._remember(state, message, "invalid_request", {})

        if "patient_mode" in slots:
            mode = str(slots["patient_mode"])
            if mode == "registered":
                state.patient_mode = "registered"
                state.patient_lookup_failed = False
            elif mode == "new":
                state.patient_mode = "new"
                state.patient_lookup_failed = True

        # New explicit intent starts/replaces a workflow; follow-up slots update it.
        continue_search_as_booking = (
            parsed.intent == "book_appointment"
            and state.intent == "find_nearest_slots"
            and state.selected_slot is not None
        )
        book_after_doctor_search = (
            parsed.intent == "book_appointment"
            and state.intent == "find_doctors"
            and state.specialty_name is not None
        )
        if continue_search_as_booking or book_after_doctor_search:
            state.intent = "book_appointment"
        elif (
            parsed.intent in {"find_nearest_slots", "find_slots"}
            and state.intent == "book_appointment"
        ):
            state.nearest_requested = True
            state.date = None
            state.available_slots.clear()
            state.selected_slot = None
        elif parsed.intent in {
            "book_appointment",
            "cancel_appointment",
            "reschedule_appointment",
            "get_appointments",
            "get_appointment",
            "find_doctors",
            "find_nearest_slots",
        }:
            keep_booking_context = (
                state.intent == "find_doctors"
                and parsed.intent == "book_appointment"
                and state.specialty_name is not None
            )
            if (parsed.intent != state.intent and not keep_booking_context) or state.phase in {
                "COMPLETED",
                "CANCELLED",
                "ERROR",
                "START",
            }:
                state.clear_workflow()
            state.intent = parsed.intent
        elif state.intent is None and parsed.intent == "unknown_request" and not slots:
            return self._remember(state, message, "unknown_request", {})
        elif parsed.intent == "unknown_request" and not slots and state.awaiting_input:
            return self._remember(
                state,
                message,
                "clarification_repeat",
                {"prompt": state.pending_question or state.last_response or ""},
            )

        if state.intent is None and parsed.intent == "unknown_request" and "specialty" in slots:
            state.intent = "book_appointment"

        if "specialty" in slots:
            state.specialty_name = slots["specialty"]
            state.specialty_id = None
            state.doctor_id = None
            state.available_slots.clear()
            state.selected_slot = None
            state.pending_action = None
            state.phase = "COLLECTING_DATA"
        if "doctor" in slots:
            state.doctor_name = slots["doctor"]
        if "date" in normalized:
            new_date = normalized["date"]
            if isinstance(new_date, date_type) and state.date != new_date:
                state.available_slots.clear()
                state.selected_slot = None
                state.pending_action = None
            if isinstance(new_date, date_type):
                state.date = new_date
        if "time_after" in normalized:
            new_time_after = normalized["time_after"]
            if isinstance(new_time_after, time_type) and state.time_after != new_time_after:
                state.available_slots.clear()
                state.selected_slot = None
                state.pending_action = None
            if isinstance(new_time_after, time_type):
                state.time_after = new_time_after
        if "time_at" in normalized:
            new_time_at = normalized["time_at"]
            if isinstance(new_time_at, time_type) and state.time_at != new_time_at:
                state.available_slots.clear()
                state.selected_slot = None
                state.pending_action = None
            if isinstance(new_time_at, time_type):
                state.time_at = new_time_at
        if "city" in normalized:
            new_city = str(normalized["city"])
            if state.tenant_locked:
                if state.clinic_city and new_city.casefold() != state.clinic_city.casefold():
                    return self._remember(
                        state,
                        message,
                        "tenant_mismatch",
                        {"clinic_city": state.clinic_city},
                    )
                state.clinic_city = new_city
            elif state.clinic_city != new_city:
                state.clinic_id = None
                state.clinic_timezone = None
                state.patient_id = None
                state.patient_name = None
                state.available_slots.clear()
                state.selected_slot = None
                state.pending_action = None
                state.clinic_city = new_city
        if "phone" in slots and not state.tenant_locked:
            state.patient_phone = slots["phone"]
            state.patient_lookup_failed = False
        if "full_name" in slots and not state.tenant_locked:
            state.patient_full_name = slots["full_name"]

        if state.intent == "find_doctors":
            return await self._find_doctors(session, state, message)
        if state.intent == "find_nearest_slots":
            return await self._search_nearest(session, state, message, parsed)
        if state.intent == "book_appointment":
            return await self._book(session, state, message, parsed)
        if state.intent in {
            "cancel_appointment",
            "reschedule_appointment",
            "get_appointments",
            "get_appointment",
        }:
            return await self._appointments(session, state, message, parsed)
        return self._remember(state, message, "unknown_request", {})

    async def _book(
        self, session: AsyncSession, state: DialogueState, message: str, parsed: NluParse
    ) -> str:
        if not state.specialty_name:
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(
                state, message, "clarification_required", {"missing": ["specialty"]}
            )
        nearest = state.nearest_requested or parsed.intent == "find_nearest_slots"
        if state.date is None and not nearest:
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(state, message, "clarification_required", {"missing": ["date"]})
        clinic_id = await self._clinic(session, state, message)
        if clinic_id is None:
            retry = state.phase == "WAITING_CLARIFICATION"
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(
                state,
                message,
                "clarification_required",
                {
                    "missing": ["clinic"],
                    "specialty": state.specialty_name,
                    "retry": retry,
                },
            )
        state.clinic_id = clinic_id
        specialties = await self._call(
            session, {"intent": "find_specialties", "clinic_id": clinic_id}
        )
        canonical = state.specialty_name.casefold()
        candidates = specialties.details.get("specialties", [])
        matches = [
            item
            for item in candidates
            if isinstance(item, dict) and str(item.get("name", "")).casefold() == canonical
        ]
        # Rules NLU canonical labels are English IDs in this MVP backend.
        if not matches:
            matches = [
                item
                for item in candidates
                if isinstance(item, dict) and canonical in str(item.get("name", "")).casefold()
            ]
        if len(matches) != 1:
            return self._remember(
                state, message, "specialty_not_available", {"specialty": state.specialty_name}
            )
        state.specialty_id = int(matches[0]["id"])
        request = {
            "intent": "find_nearest_slots" if nearest else "find_slots",
            "clinic_id": clinic_id,
            "specialty_id": None if state.doctor_id is not None else state.specialty_id,
            "doctor_id": state.doctor_id,
            "date": state.date,
            "days_ahead": 14 if nearest else None,
            "time_after": state.time_after,
            "time_at": state.time_at,
        }
        named_slots = [
            slot
            for slot in state.available_slots
            if state.doctor_name
            and self._matches_doctor_name(
                state.doctor_name,
                str((slot.get("doctor") or {}).get("full_name", "")),
            )
        ]
        if parsed.intent == "select_option" and state.available_slots:
            selected = self._select(state.available_slots, parsed, state.clinic_timezone)
            if selected is None:
                return self._remember(
                    state, message, "invalid_selection", {"count": len(state.available_slots)}
                )
            state.selected_slot = selected
        elif state.doctor_name and state.available_slots:
            if len(named_slots) == 1:
                state.selected_slot = named_slots[0]
            else:
                return self._remember(
                    state,
                    message,
                    "invalid_selection" if not named_slots else "clarification_required",
                    {"count": len(state.available_slots)},
                )
        elif state.selected_slot is None:
            state.phase = "SEARCHING"
            result = await self._call(session, request)
            timezone_name = result.details.get("timezone")
            if isinstance(timezone_name, str):
                state.clinic_timezone = timezone_name
            result_date = result.details.get("date")
            if nearest and isinstance(result_date, str):
                try:
                    state.date = date_type.fromisoformat(result_date)
                except ValueError:
                    state.date = None
            if result.code == "NO_SLOTS_AVAILABLE":
                return self._remember(state, message, "no_available_slots", result.details)
            raw = result.details.get("slots", [])
            state.available_slots = [item for item in raw if isinstance(item, dict)]
            if not state.available_slots:
                return self._remember(state, message, "no_available_slots", {})
            if len(state.available_slots) > 1:
                state.phase = "SELECTING"
                return self._remember(
                    state,
                    message,
                    "appointment_slots_found",
                    {"slots": state.available_slots[:5]},
                )
            else:
                state.selected_slot = state.available_slots[0]
        if state.patient_id is None and not state.tenant_locked:
            if state.patient_mode is None:
                state.phase = "WAITING_CLARIFICATION"
                return self._remember(
                    state, message, "clarification_required", {"missing": ["patient"]}
                )
            if state.patient_mode == "registered" and not state.patient_phone:
                state.phase = "WAITING_CLARIFICATION"
                return self._remember(state, message, "patient_phone_required", {})
            if state.patient_mode == "new" and not state.patient_full_name:
                state.phase = "WAITING_CLARIFICATION"
                return self._remember(state, message, "patient_name_required", {})
            if state.patient_mode == "new" and not state.patient_phone:
                state.phase = "WAITING_CLARIFICATION"
                return self._remember(state, message, "patient_phone_required", {})
        patient_id = await self._patient(session, state)
        if patient_id is None and not (
            (state.patient_mode == "new" or state.tenant_locked)
            and state.patient_full_name
            and state.patient_phone
        ):
            state.phase = "WAITING_CLARIFICATION"
            if state.patient_lookup_failed:
                return self._remember(state, message, "patient_not_found", {})
            return self._remember(
                state, message, "clarification_required", {"missing": ["patient"]}
            )
        start = datetime.fromisoformat(str(state.selected_slot["starts_at"]))
        doctor = state.selected_slot.get("doctor")
        state.doctor_id = int(doctor["id"]) if isinstance(doctor, dict) else None
        state.doctor_name = str(doctor.get("full_name", "")) if isinstance(doctor, dict) else None
        state.pending_action = {
            "intent": "book_appointment",
            "clinic_id": clinic_id,
            "doctor_id": state.doctor_id,
            "starts_at": start,
            "confirmed": False,
        }
        if patient_id is not None:
            state.pending_action["patient_id"] = patient_id
        else:
            state.pending_action["full_name"] = state.patient_full_name
            state.pending_action["phone"] = state.patient_phone
        preview = await self._call(session, state.pending_action)
        if preview.status != "confirmation_required":
            state.phase = "ERROR"
            return self._remember(state, message, "backend_error", {})
        state.phase = "CONFIRMING"
        state.confirmation_state = "pending"
        return self._remember(
            state,
            message,
            "confirmation_required",
            {
                "action": "book",
                "slot": state.selected_slot,
                "patient_name": state.patient_name or state.patient_full_name,
                "new_patient": patient_id is None,
            },
        )

    async def _find_doctors(self, session: AsyncSession, state: DialogueState, message: str) -> str:
        if state.selection_kind == "doctor" and state.appointment_candidates:
            parsed = parse_utterance(message)
            selected = (
                self._select(state.appointment_candidates, parsed, state.clinic_timezone)
                if parsed.intent == "select_option"
                else None
            )
            spoken_doctor = state.doctor_name or message
            if selected is None:
                matching = [
                    doctor
                    for doctor in state.appointment_candidates
                    if self._matches_doctor_name(spoken_doctor, str(doctor.get("full_name", "")))
                ]
                selected = matching[0] if len(matching) == 1 else None
            if selected is not None:
                state.doctor_id = int(selected["id"])
                state.doctor_name = str(selected.get("full_name", ""))
                state.appointment_candidates.clear()
                state.selection_kind = None
                return self._remember(
                    state, message, "doctor_found", {"names": [state.doctor_name]}
                )
        if not state.specialty_name:
            return self._remember(
                state, message, "clarification_required", {"missing": ["specialty"]}
            )
        clinic_id = await self._clinic(session, state, message)
        if clinic_id is None:
            retry = state.phase == "WAITING_CLARIFICATION"
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(
                state,
                message,
                "clarification_required",
                {
                    "missing": ["clinic"],
                    "specialty": state.specialty_name,
                    "retry": retry,
                },
            )
        state.clinic_id = clinic_id
        specialty_id = await self._resolve_specialty_id(session, clinic_id, state.specialty_name)
        if specialty_id is None:
            return self._remember(
                state,
                message,
                "specialty_not_available",
                {"specialty": state.specialty_name},
            )
        result = await self._call(
            session,
            {
                "intent": "find_doctors",
                "clinic_id": clinic_id,
                "specialty_id": specialty_id,
            },
        )
        doctors = result.details.get("doctors", [])
        names = (
            [
                str(doctor["full_name"])
                for doctor in doctors
                if isinstance(doctor, dict) and doctor.get("full_name")
            ]
            if isinstance(doctors, list)
            else []
        )
        if not names:
            return self._remember(state, message, "no_doctors_found", {})
        state.appointment_candidates = (
            [doctor for doctor in doctors if isinstance(doctor, dict) and doctor.get("id")]
            if isinstance(doctors, list)
            else []
        )
        if len(state.appointment_candidates) == 1:
            doctor = state.appointment_candidates[0]
            state.doctor_id = int(doctor["id"])
            state.doctor_name = str(doctor.get("full_name", ""))
            state.selection_kind = None
        elif len(state.appointment_candidates) > 1:
            state.selection_kind = "doctor"
        state.phase = "COMPLETED"
        return self._remember(
            state,
            message,
            "doctors_found",
            {"names": names, "single_doctor": len(state.appointment_candidates) == 1},
        )

    async def _search_nearest(
        self,
        session: AsyncSession,
        state: DialogueState,
        message: str,
        parsed: NluParse,
    ) -> str:
        if not state.specialty_name:
            return self._remember(
                state, message, "clarification_required", {"missing": ["specialty"]}
            )
        if state.available_slots and parsed.intent == "select_option":
            selected = self._select(state.available_slots, parsed, state.clinic_timezone)
            if selected is None:
                return self._remember(
                    state,
                    message,
                    "invalid_selection",
                    {"count": len(state.available_slots)},
                )
            state.selected_slot = selected
            state.phase = "COMPLETED"
            return self._remember(
                state,
                message,
                "appointment_slot_selected",
                {"slot": selected},
            )
        clinic_id = await self._clinic(session, state, message)
        if clinic_id is None:
            retry = state.phase == "WAITING_CLARIFICATION"
            state.phase = "WAITING_CLARIFICATION"
            return self._remember(
                state,
                message,
                "clarification_required",
                {
                    "missing": ["clinic"],
                    "specialty": state.specialty_name,
                    "retry": retry,
                },
            )
        state.clinic_id = clinic_id
        specialty_id = await self._resolve_specialty_id(session, clinic_id, state.specialty_name)
        if specialty_id is None:
            return self._remember(
                state,
                message,
                "specialty_not_available",
                {"specialty": state.specialty_name},
            )
        result = await self._call(
            session,
            {
                "intent": "find_nearest_slots",
                "clinic_id": clinic_id,
                "specialty_id": specialty_id,
                "date": state.date,
                "days_ahead": 14,
                "time_after": state.time_after,
                "time_at": state.time_at,
            },
        )
        timezone_name = result.details.get("timezone")
        if isinstance(timezone_name, str):
            state.clinic_timezone = timezone_name
        date_value = result.details.get("date")
        if isinstance(date_value, str):
            try:
                state.date = date_type.fromisoformat(date_value)
            except ValueError:
                state.date = None
        slots = result.details.get("slots", [])
        state.available_slots = [slot for slot in slots if isinstance(slot, dict)]
        if not state.available_slots:
            return self._remember(state, message, "no_available_slots", {})
        state.phase = "SELECTING"
        return self._remember(
            state,
            message,
            "appointment_slots_found",
            {"slots": state.available_slots[:5]},
        )

    async def _resolve_specialty_id(
        self, session: AsyncSession, clinic_id: int, specialty_name: str
    ) -> int | None:
        result = await self._call(session, {"intent": "find_specialties", "clinic_id": clinic_id})
        specialties = result.details.get("specialties", [])
        matching = [
            item
            for item in specialties
            if isinstance(item, dict)
            and str(item.get("name", "")).casefold() == specialty_name.casefold()
        ]
        if len(matching) != 1:
            return None
        return int(matching[0]["id"])

    async def _appointments(
        self, session: AsyncSession, state: DialogueState, message: str, parsed: NluParse
    ) -> str:
        clinic_id = await self._clinic(session, state, message)
        if clinic_id is None:
            return self._remember(state, message, "clarification_required", {"missing": ["clinic"]})
        state.clinic_id = clinic_id
        if state.patient_id is None and not state.tenant_locked:
            if state.patient_mode is None:
                return self._remember(
                    state, message, "clarification_required", {"missing": ["patient"]}
                )
            if state.patient_mode == "registered" and not state.patient_phone:
                return self._remember(state, message, "patient_phone_required", {})
            if state.patient_mode == "new":
                return self._remember(state, message, "appointment_not_found", {})
        patient_id = await self._patient(session, state)
        if patient_id is None:
            if state.patient_lookup_failed:
                return self._remember(state, message, "patient_not_found", {})
            return self._remember(state, message, "patient_phone_required", {})
        if (
            state.intent == "reschedule_appointment"
            and state.phase == "SELECTING"
            and state.appointment_id is not None
            and state.available_slots
        ):
            slot = self._select(state.available_slots, parsed, state.clinic_timezone)
            if slot is None:
                return self._remember(
                    state,
                    message,
                    "invalid_selection",
                    {"count": len(state.available_slots)},
                )
            action = {
                "intent": "reschedule_appointment",
                "clinic_id": clinic_id,
                "appointment_id": state.appointment_id,
                "new_starts_at": datetime.fromisoformat(str(slot["starts_at"])),
                "confirmed": False,
            }
            preview = await self._call(session, action)
            if preview.status != "confirmation_required":
                return self._remember(state, message, "backend_error", {})
            state.pending_action = action
            state.phase = "CONFIRMING"
            state.confirmation_state = "pending"
            return self._remember(
                state,
                message,
                "confirmation_required",
                {"action": "reschedule", **preview.details},
            )
        result = await self._call(
            session,
            {"intent": "get_appointments", "clinic_id": clinic_id, "patient_id": patient_id},
        )
        items = [item for item in result.details.get("appointments", []) if isinstance(item, dict)]
        if not items:
            return self._remember(state, message, "appointment_not_found", {})
        if state.intent in {"get_appointments", "get_appointment"}:
            state.phase = "COMPLETED"
            return self._remember(state, message, "appointment_status", {"appointments": items})
        if not state.appointment_candidates:
            state.appointment_candidates = [
                item for item in items if item.get("status") == "booked"
            ]
        if parsed.intent == "select_option":
            selected = self._select(state.appointment_candidates, parsed, state.clinic_timezone)
            if selected is None:
                return self._remember(
                    state,
                    message,
                    "invalid_selection",
                    {"count": len(state.appointment_candidates)},
                )
            state.appointment_id = int(selected["id"])
        elif len(state.appointment_candidates) == 1:
            state.appointment_id = int(state.appointment_candidates[0]["id"])
        elif len(state.appointment_candidates) > 1:
            return self._remember(
                state,
                message,
                "clarification_required",
                {
                    "missing": ["appointment_selection"],
                    "appointments": state.appointment_candidates,
                },
            )
        else:
            return self._remember(state, message, "appointment_not_found", {})
        if state.intent == "cancel_appointment":
            action = {
                "intent": "cancel_appointment",
                "clinic_id": clinic_id,
                "appointment_id": state.appointment_id,
                "confirmed": False,
            }
        else:
            if state.date is None:
                return self._remember(
                    state, message, "clarification_required", {"missing": ["date"]}
                )
            # Reschedule reuses appointment doctor; find new-day slots.
            appt = next(
                item
                for item in state.appointment_candidates
                if item.get("id") == state.appointment_id
            )
            start_day = state.date.isoformat()
            avail = await self._call(
                session,
                {
                    "intent": "find_slots",
                    "clinic_id": clinic_id,
                    "doctor_id": appt.get("doctor_id"),
                    "date": start_day,
                    "time_after": state.time_after,
                },
            )
            timezone_name = avail.details.get("timezone")
            if isinstance(timezone_name, str):
                state.clinic_timezone = timezone_name
            choices = [item for item in avail.details.get("slots", []) if isinstance(item, dict)]
            if not choices:
                return self._remember(state, message, "no_available_slots", {})
            state.available_slots = choices
            selection = (
                self._select(choices, parsed, state.clinic_timezone)
                if parsed.intent == "select_option"
                else None
            )
            if selection is None and len(choices) > 1:
                state.phase = "SELECTING"
                return self._remember(
                    state, message, "appointment_slots_found", {"slots": choices[:5]}
                )
            slot = selection or choices[0]
            action = {
                "intent": "reschedule_appointment",
                "clinic_id": clinic_id,
                "appointment_id": state.appointment_id,
                "new_starts_at": datetime.fromisoformat(str(slot["starts_at"])),
                "confirmed": False,
            }
        preview = await self._call(session, action)
        if preview.status != "confirmation_required":
            return self._remember(state, message, "backend_error", {})
        state.pending_action = action
        state.phase = "CONFIRMING"
        state.confirmation_state = "pending"
        return self._remember(
            state,
            message,
            "confirmation_required",
            {
                "action": "reschedule"
                if action["intent"] == "reschedule_appointment"
                else "cancel",
                **preview.details,
            },
        )

    async def _clinic(
        self, session: AsyncSession, state: DialogueState, message: str
    ) -> int | None:
        if state.clinic_id is not None:
            return state.clinic_id
        result = await self._call(session, {"intent": "find_clinics", "city": state.clinic_city})
        clinics = [item for item in result.details.get("clinics", []) if isinstance(item, dict)]
        if state.clinic_city:
            key = state.clinic_city.casefold()
            clinics = [item for item in clinics if key in str(item.get("city", "")).casefold()]
        if len(clinics) == 1:
            return int(clinics[0]["id"])
        return None

    async def _patient(self, session: AsyncSession, state: DialogueState) -> int | None:
        if state.patient_id:
            return state.patient_id
        if state.clinic_id is None:
            # clinic resolution caller sets this after _clinic
            return None
        if state.patient_mode == "new":
            return None
        if state.patient_mode != "registered" and not state.tenant_locked:
            return None
        if state.patient_phone:
            result = await self._call(
                session,
                {
                    "intent": "find_patient",
                    "clinic_id": state.clinic_id,
                    "phone": state.patient_phone,
                },
            )
            patient = result.details.get("patient")
            if isinstance(patient, dict):
                state.patient_id = int(patient["id"])
                state.patient_name = str(patient.get("full_name", ""))
                state.patient_lookup_failed = False
                return state.patient_id
            state.patient_lookup_failed = True
            return None
        return None

    async def _call(self, session: AsyncSession, payload: dict[str, Any]) -> AssistantResult:
        return await self.orchestrator.handle(session, AssistantRequest.model_validate(payload))

    @staticmethod
    def _select(
        items: list[dict[str, Any]], parsed: NluParse, timezone_name: str | None = None
    ) -> dict[str, Any] | None:
        candidate = parsed.slots.get("selection")
        if candidate is None:
            candidate = parsed.slots.get("selection_time")
            if candidate is not None:
                timezone = ZoneInfo(timezone_name) if timezone_name else ZoneInfo("UTC")
                matches = []
                for item in items:
                    starts_at = item.get("starts_at")
                    if not isinstance(starts_at, str):
                        continue
                    try:
                        local_start = datetime.fromisoformat(starts_at).astimezone(timezone)
                    except ValueError:
                        continue
                    if local_start.strftime("%H:%M") == candidate.value:
                        matches.append(item)
                return matches[0] if len(matches) == 1 else None
        if candidate is None:
            return None
        if candidate.value == "earliest":
            return min(items, key=lambda x: str(x.get("starts_at", "")))
        if candidate.value == "latest":
            return max(items, key=lambda x: str(x.get("starts_at", "")))
        try:
            index = int(candidate.value) - 1
        except ValueError:
            return None
        return items[index] if 0 <= index < len(items) else None

    @staticmethod
    def _extract_contextual_name(message: str) -> str | None:
        """Accept a plain full name only while the dialogue is explicitly asking for it."""
        candidate = re.sub(r"[^а-яё -]", " ", message.casefold().replace("ё", "е"))
        candidate = re.sub(
            r"\b(?:меня зовут|мо[её] имя|я впервые|впервые|записываюсь впервые|"
            r"раньше не был\w*|имя и фамил\w*|имя|фамил\w*|это|я)\b",
            " ",
            candidate,
        )
        words = candidate.split()
        ignored = {
            "ты",
            "вы",
            "что",
            "делать",
            "помоги",
            "помогите",
            "даун",
            "дебил",
            "идиот",
            "дурак",
            "дура",
            "тупой",
            "тупая",
            "впервые",
            "номер",
            "телефон",
            "проверьте",
            "проверить",
            "зарегистрирован",
            "зарегистрирована",
            "если",
            "или",
        }
        if len(words) not in {2, 3} or any(word in ignored for word in words):
            return None
        return " ".join(word.capitalize() for word in words)

    @staticmethod
    def _extract_patient_mode(message: str) -> str | None:
        normalized = " ".join(message.casefold().split())
        if re.search(
            r"\b(?:впервые|новый пациент|новая пациентка|раньше не был\w*|"
            r"раньше не обращал\w*ся|нет карты)\b",
            normalized,
        ):
            return "new"
        if re.search(
            r"\b(?:уже был\w*|уже обращал\w*ся|я ваш пациент|я пациент|"
            r"зарегистрирован\w*|есть карта|есть карточка)\b",
            normalized,
        ):
            return "registered"
        return None

    @staticmethod
    def _matches_doctor_name(spoken_name: str, full_name: str) -> bool:
        """Match a spoken inflected surname against a backend doctor name."""

        def key(word: str) -> str:
            value = word.casefold().replace("ё", "е")
            for ending in (
                "евыми",
                "овыми",
                "ого",
                "ему",
                "ому",
                "ыми",
                "ими",
                "ой",
                "ый",
                "ий",
                "ая",
                "ую",
                "ых",
                "ом",
                "ем",
                "у",
                "а",
                "е",
                "ы",
                "и",
            ):
                if len(value) > len(ending) + 3 and value.endswith(ending):
                    return value[: -len(ending)]
            return value

        spoken = {key(part) for part in spoken_name.split()}
        backend = {key(part) for part in full_name.split()}
        return bool(spoken & backend)

    @staticmethod
    def _event_for(result: AssistantResult) -> str:
        return {
            "APPOINTMENT_BOOKED": "appointment_booked",
            "APPOINTMENT_CANCELLED": "appointment_cancelled",
            "APPOINTMENT_RESCHEDULED": "appointment_rescheduled",
        }.get(result.code, "backend_error")

    @staticmethod
    def _remember(state: DialogueState, message: str, event: str, payload: dict[str, Any]) -> str:
        response = render_event(event, timezone=state.clinic_timezone, **payload)
        expected: str | None = None
        missing = payload.get("missing", [])
        if event == "clarification_required" and isinstance(missing, list):
            expected = next(
                (
                    name
                    for name in (
                        "specialty",
                        "date",
                        "clinic",
                        "appointment_selection",
                    )
                    if name in missing
                ),
                "patient_mode" if "patient" in missing else None,
            )
        elif event in {"appointment_slots_found", "invalid_selection"}:
            expected = "slot"
        elif event == "patient_phone_required":
            expected = "phone"
        elif event == "patient_name_required":
            expected = "full_name"
        elif event == "patient_not_found":
            expected = "patient_mode"
        elif event == "confirmation_required":
            expected = "confirmation"
        elif event == "health_concern":
            expected = "specialty"
        elif event == "doctor_found":
            expected = "doctor_booking"
        elif event == "invalid_patient_name":
            expected = "full_name"
        elif event == "doctors_found":
            expected = (
                "doctor_choice"
                if state.selection_kind == "doctor"
                else "doctor_booking"
                if payload.get("single_doctor")
                else None
            )
        elif event in {"unknown_request", "workflow_cancelled", "greeting"}:
            expected = "intent"
        elif event in {"clarification_repeat", "continue_dialogue"}:
            expected = state.awaiting_input
        elif event == "no_available_slots":
            expected = "date_or_nearest"
        elif event == "specialty_not_available":
            expected = "clinic"
        state.awaiting_input = expected
        if expected is not None and event != "clarification_repeat":
            state.pending_question = response
        elif expected is None:
            state.pending_question = None
        state.last_user_message_hash = sha256(message.encode()).hexdigest()
        state.last_response = response
        state.touch()
        return response
