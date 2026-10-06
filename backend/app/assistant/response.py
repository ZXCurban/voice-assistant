"""Deterministic event-based voice response rendering."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.assistant.schemas import AssistantResult

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)

_SPECIALTY_LABELS = {
    "cardiology": "кардиолога",
    "dermatology": "дерматолога",
    "neurology": "невролога",
    "ophthalmology": "офтальмолога",
    "pediatrics": "педиатра",
    "dentistry": "стоматолога",
    "surgery": "хирурга",
    "otolaryngology": "ЛОР-врача",
    "critical_care": "реаниматолога",
    "therapist": "терапевта",
}


def event_for(result: AssistantResult) -> dict[str, Any]:
    """Convert a service result to a safe response event without inventing facts."""
    return {"event": result.code.lower(), "status": result.status, "details": result.details}


def render_response(result: AssistantResult) -> str:
    event_payload = event_for(result)
    details = event_payload["details"]
    event = result.status if result.status != "success" else event_payload["event"]
    if result.code == "UNKNOWN_REQUEST":
        event = "unknown_request"
    if result.status == "need_clarification" and result.code == "CLINIC_REQUIRED":
        event = "clinic_required"
    elif result.status == "need_clarification" and result.code == "DATE_REQUIRED":
        event = "date_required"
    elif result.status == "need_clarification" and result.code == "SPECIALTY_OR_DOCTOR_REQUIRED":
        event = "specialty_required"
    if result.code == "NO_SLOTS_AVAILABLE":
        event = "no_available_slots"
    if result.status == "success" and result.code == "OK" and "clinics" in details:
        event = "clinics_found" if details.get("clinics") else "no_clinics_found"
    if result.status == "success" and result.code == "OK" and "slots" in details:
        event = "slots_found"
    if result.status == "success" and result.code == "OK":
        if "appointments" in details:
            event = "appointments_found"
        elif "doctors" in details:
            event = "doctors_found"
        elif "doctor" in details:
            event = "doctor_found"
        elif "specialties" in details:
            event = "specialties_found"
    time_value = ""
    clinic_names: list[str] = []
    names: list[str] = []
    appointments: list[dict[str, Any]] = []
    clinics = details.get("clinics")
    if isinstance(clinics, list):
        clinic_names = [
            str(item["name"])
            for item in clinics
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:3]
    doctors = details.get("doctors")
    if isinstance(doctors, list):
        names = [
            str(item["full_name"])
            for item in doctors
            if isinstance(item, dict) and isinstance(item.get("full_name"), str)
        ][:5]
    doctor = details.get("doctor")
    if isinstance(doctor, dict) and isinstance(doctor.get("full_name"), str):
        names = [str(doctor["full_name"])]
    specialties = details.get("specialties")
    if isinstance(specialties, list):
        names = [
            str(item["name"])
            for item in specialties
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:5]
    appointment_items = details.get("appointments")
    if isinstance(appointment_items, list):
        appointments = [item for item in appointment_items if isinstance(item, dict)]
    if result.status == "success" and event == "slots_found":
        slots = details.get("slots")
        if isinstance(slots, list) and slots:
            first = slots[0]
            if isinstance(first, dict):
                timestamp = first.get("starts_at")
                timezone_name = details.get("timezone")
                if isinstance(timestamp, str) and isinstance(timezone_name, str):
                    try:
                        time_value = (
                            datetime.fromisoformat(timestamp)
                            .astimezone(ZoneInfo(timezone_name))
                            .strftime("%d.%m в %H:%M")
                        )
                    except ValueError:
                        time_value = ""
    if event == "ok":
        event = "request_succeeded"
    if result.status == "success" and event not in {
        "appointment_booked",
        "appointment_cancelled",
        "appointment_rescheduled",
        "appointment_completed",
        "patient_created",
        "clinics_found",
        "no_clinics_found",
        "slots_found",
        "no_available_slots",
        "appointments_found",
        "doctors_found",
        "doctor_found",
        "specialties_found",
        "request_succeeded",
    }:
        event = "request_succeeded"
    template = _TEMPLATES.get_template("responses.ru.j2")
    return str(
        template.render(
            event=event,
            time=time_value,
            clinics=clinic_names,
            appointments=appointments,
            names=names,
            message=result.message,
        )
    ).strip()


def render_event(event: str, **payload: Any) -> str:
    """Render a dialogue event; this layer has no backend access by design."""
    template = _TEMPLATES.get_template("responses.ru.j2")
    specialty = payload.get("specialty")
    if isinstance(specialty, str):
        payload["specialty_label"] = _SPECIALTY_LABELS.get(specialty, specialty)
    slots = payload.get("slots", [])
    timezone_name = payload.get("timezone")
    timezone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else None

    def format_datetime(value: Any) -> str:
        parsed = datetime.fromisoformat(str(value))
        if timezone is not None:
            parsed = parsed.astimezone(timezone)
        return parsed.strftime("%d.%m в %H:%M")

    if isinstance(slots, list):
        rendered_slots = []
        for index, slot in enumerate(slots[:5], 1):
            starts_at = slot.get("starts_at") if isinstance(slot, dict) else None
            try:
                label = format_datetime(starts_at)
            except ValueError:
                label = "время не указано"
            rendered_slots.append({"index": index, "label": label})
        payload["rendered_slots"] = rendered_slots
    for field in ("slot", "new_slot"):
        value = payload.get(field)
        if isinstance(value, dict):
            try:
                payload[f"{field}_label"] = format_datetime(value["starts_at"])
            except (KeyError, ValueError):
                payload[f"{field}_label"] = ""
    appointment = payload.get("appointment")
    if isinstance(appointment, dict):
        try:
            payload["appointment_label"] = format_datetime(appointment["starts_at"])
        except (KeyError, ValueError):
            payload["appointment_label"] = ""
    appointments = payload.get("appointments")
    if isinstance(appointments, list):
        rendered_appointments = []
        for item in appointments[:5]:
            if not isinstance(item, dict):
                continue
            try:
                label = format_datetime(item["starts_at"])
            except (KeyError, ValueError):
                label = "дата не указана"
            rendered_appointments.append(
                {
                    "label": label,
                    "status": str(item.get("status", "неизвестен")),
                    "doctor": str(
                        (item.get("doctor") or {}).get("full_name", "")
                        if isinstance(item.get("doctor"), dict)
                        else ""
                    ),
                }
            )
        payload["rendered_appointments"] = rendered_appointments
    return str(template.render(event=event, **payload)).strip()
