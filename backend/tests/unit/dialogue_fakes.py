"""Test doubles for the dialogue layer: scripted NLU + in-memory backend.

`FakeBackend` mimics the AssistantOrchestrator contract (statuses, codes
and `details` shapes) closely enough to drive the DialogueManager without
a database; the same flows run against the real orchestrator in
tests/integration/test_nlu_chat_flow.py.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.nlu.schemas import NluParse

Script = dict[str, tuple[str, dict[str, str]]]

LOCAL_TIMES = ("09:00", "09:30", "10:00", "14:00", "14:30", "15:00")


class ScriptedEngine:
    """NluEngine that answers from a prepared {text: (intent, slots)} table.

    Unknown text → an unconfident `unknown_request` without slots, like the
    real model on gibberish. Every context the dialogue showed to the model
    is recorded in `contexts`.
    """

    version = "scripted"

    def __init__(self, script: Script, *, confidence: float = 0.99) -> None:
        self._script = script
        self._confidence = confidence
        self.contexts: list[str] = []

    def parse(self, text: str, last_response: str = "") -> NluParse:
        self.contexts.append(last_response)
        if text not in self._script:
            return NluParse(intent="unknown_request", confidence=0.4, confident=False)
        intent, slots = self._script[text]
        return NluParse(
            intent=intent, confidence=self._confidence, confident=True, slots=dict(slots)
        )


def _result(
    status: str, code: str, details: dict[str, Any] | None = None, *, confirm: bool = False
) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "message": code,
        "requires_confirmation": confirm,
        "details": details or {},
    }


class FakeBackend:
    """Two clinics (Moscow, Kazan) with a few doctors and daily slots."""

    def __init__(self) -> None:
        self.today = date(2026, 10, 8)  # Thursday
        self.clinics = [
            {
                "id": 1,
                "name": "Клиника Северная звезда",
                "city": "Москва",
                "address": "Тверской бульвар, 12, Москва",
                "timezone": "Europe/Moscow",
            },
            {
                "id": 2,
                "name": "Волга Плюс",
                "city": "Казань",
                "address": "ул. Баумана, 20, Казань",
                "timezone": "Europe/Moscow",
            },
        ]
        # clinic_id -> [(doctor_id, full_name, specialty)]
        self.doctors = {
            1: [(11, "Андрей Волков", "cardiology"), (12, "Ольга Морозова", "dermatology")],
            2: [(21, "Анна Смирнова", "pediatrics"), (22, "Елена Кузнецова", "neurology")],
        }
        self.patients: list[dict[str, Any]] = [
            {"id": 1, "clinic_id": 1, "full_name": "Иван Петров", "phone": "+79210000001"}
        ]
        self.appointments: list[dict[str, Any]] = []
        self.busy: set[tuple[int, str]] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- helpers -----------------------------------------------------------

    def tool_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def _clinic(self, clinic_id: int) -> dict[str, Any]:
        return next(c for c in self.clinics if c["id"] == clinic_id)

    def instant(self, clinic_id: int, day: date, hm: str) -> str:
        hour, minute = map(int, hm.split(":"))
        local = datetime.combine(
            day, time(hour, minute), ZoneInfo(self._clinic(clinic_id)["timezone"])
        )
        return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _slots_for(self, clinic_id: int, doctor_id: int, day: date) -> list[dict[str, Any]]:
        if day <= self.today:
            return []
        doctor = next(d for d in self.doctors[clinic_id] if d[0] == doctor_id)
        booked = {a["starts_at"] for a in self.appointments if a["status"] == "booked"}
        slots = []
        for hm in LOCAL_TIMES:
            starts_at = self.instant(clinic_id, day, hm)
            if (doctor_id, starts_at) in self.busy or starts_at in booked:
                continue
            slots.append(
                {
                    "doctor": {"id": doctor[0], "full_name": doctor[1]},
                    "specialty": {"id": 1, "name": doctor[2]},
                    "starts_at": starts_at,
                    "ends_at": starts_at,
                    "room": None,
                }
            )
        return slots

    def _targets(self, clinic_id: int, args: dict[str, Any]) -> list[int] | dict[str, Any]:
        if "doctor_id" in args:
            return [args["doctor_id"]]
        wanted = str(args["specialty_name"]).lower()
        found = [d[0] for d in self.doctors[clinic_id] if d[2] == wanted]
        return found or _result("not_found", "SPECIALTY_NOT_FOUND")

    def _patient_payload(self, patient: dict[str, Any]) -> dict[str, Any]:
        return {"id": patient["id"], "full_name": patient["full_name"], "phone": patient["phone"]}

    def _appointment_payload(self, appointment: dict[str, Any]) -> dict[str, Any]:
        clinic_id = appointment["clinic_id"]
        doctor = next(d for d in self.doctors[clinic_id] if d[0] == appointment["doctor_id"])
        patient = next(p for p in self.patients if p["id"] == appointment["patient_id"])
        return {
            "id": appointment["id"],
            "clinic_id": clinic_id,
            "status": appointment["status"],
            "starts_at": appointment["starts_at"],
            "doctor": {"id": doctor[0], "full_name": doctor[1]},
            "specialty": {"id": 1, "name": doctor[2]},
            "patient": {"id": patient["id"], "full_name": patient["full_name"]},
        }

    # -- tool entry point --------------------------------------------------

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        handler = getattr(self, f"_tool_{name}")
        result: dict[str, Any] = handler(args)
        return result

    def _tool_find_clinics(self, args: dict[str, Any]) -> dict[str, Any]:
        city = str(args.get("city", "")).lower()
        ranked = sorted(self.clinics, key=lambda c: c["city"].lower() != city)
        return _result("success", "OK", {"clinics": ranked, "matched_city": city or None})

    def _tool_find_doctors(self, args: dict[str, Any]) -> dict[str, Any]:
        clinic_id = args["clinic_id"]
        doctors = self.doctors[clinic_id]
        if "specialty_name" in args:
            wanted = str(args["specialty_name"]).lower()
            if wanted not in {d[2] for d in doctors}:
                return _result("not_found", "SPECIALTY_NOT_FOUND")
            doctors = [d for d in doctors if d[2] == wanted]
        rows = [{"id": d[0], "full_name": d[1], "specialty": {"name": d[2]}} for d in doctors]
        return _result("success", "OK", {"clinic_id": clinic_id, "doctors": rows})

    def _search(self, args: dict[str, Any], day: date) -> list[dict[str, Any]] | dict[str, Any]:
        targets = self._targets(args["clinic_id"], args)
        if isinstance(targets, dict):
            return targets
        slots: list[dict[str, Any]] = []
        for doctor_id in targets:
            slots += self._slots_for(args["clinic_id"], doctor_id, day)
        return sorted(slots, key=lambda s: s["starts_at"])

    def _tool_find_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        found = self._search(args, date.fromisoformat(args["date"]))
        if isinstance(found, dict):
            return found
        if not found:
            return _result(
                "not_found", "NO_SLOTS_AVAILABLE", {"clinic_id": args["clinic_id"], "slots": []}
            )
        return _result("success", "OK", {"clinic_id": args["clinic_id"], "slots": found})

    def _tool_find_nearest_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        start = date.fromisoformat(args["date"])
        for offset in range(args.get("days_ahead", 10)):
            day = start + timedelta(days=offset)
            found = self._search(args, day)
            if isinstance(found, dict):
                return found
            if found:
                return _result(
                    "success",
                    "OK",
                    {"clinic_id": args["clinic_id"], "date": day.isoformat(), "slots": found},
                )
        return _result(
            "not_found", "NO_SLOTS_AVAILABLE", {"clinic_id": args["clinic_id"], "slots": []}
        )

    def _tool_find_patient(self, args: dict[str, Any]) -> dict[str, Any]:
        for patient in self.patients:
            if patient["clinic_id"] == args["clinic_id"] and patient["phone"] == args["phone"]:
                return _result("success", "OK", {"patient": self._patient_payload(patient)})
        return _result("not_found", "PATIENT_NOT_FOUND")

    def _tool_book_appointment(self, args: dict[str, Any]) -> dict[str, Any]:
        clinic_id = args["clinic_id"]
        if "patient_id" in args:
            patient = next(p for p in self.patients if p["id"] == args["patient_id"])
        else:
            patient = next(
                (
                    p
                    for p in self.patients
                    if p["clinic_id"] == clinic_id and p["phone"] == args["phone"]
                ),
                None,
            )
            if patient is None:
                patient = {
                    "id": len(self.patients) + 1,
                    "clinic_id": clinic_id,
                    "full_name": args["full_name"],
                    "phone": args["phone"],
                }
                self.patients.append(patient)
        starts_at = args["starts_at"]
        offered = {
            s["starts_at"]
            for s in self._slots_for_doctor_any_day(clinic_id, args["doctor_id"], starts_at)
        }
        if starts_at not in offered:
            return _result("conflict", "SLOT_ALREADY_BOOKED")
        if not args.get("confirmed"):
            return _result(
                "confirmation_required",
                "CONFIRM_BOOKING",
                {"patient_id": patient["id"]},
                confirm=True,
            )
        appointment = {
            "id": len(self.appointments) + 1,
            "clinic_id": clinic_id,
            "doctor_id": args["doctor_id"],
            "patient_id": patient["id"],
            "starts_at": starts_at,
            "status": "booked",
        }
        self.appointments.append(appointment)
        return _result(
            "success", "APPOINTMENT_BOOKED", {"appointment": self._appointment_payload(appointment)}
        )

    def _slots_for_doctor_any_day(
        self, clinic_id: int, doctor_id: int, starts_at: str
    ) -> list[dict[str, Any]]:
        local = datetime.fromisoformat(starts_at).astimezone(
            ZoneInfo(self._clinic(clinic_id)["timezone"])
        )
        return self._slots_for(clinic_id, doctor_id, local.date())

    def _tool_get_appointments(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = [
            self._appointment_payload(a)
            for a in self.appointments
            if a["clinic_id"] == args["clinic_id"]
            and a["patient_id"] == args["patient_id"]
            and args.get("appointment_status", a["status"]) == a["status"]
        ]
        return _result("success", "OK", {"appointments": rows})

    def _booked(self, args: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (
                a
                for a in self.appointments
                if a["id"] == args["appointment_id"] and a["status"] == "booked"
            ),
            None,
        )

    def _tool_cancel_appointment(self, args: dict[str, Any]) -> dict[str, Any]:
        appointment = self._booked(args)
        if appointment is None:
            return _result("conflict", "APPOINTMENT_ALREADY_CANCELLED")
        if not args.get("confirmed"):
            return _result("confirmation_required", "CONFIRM_CANCEL", confirm=True)
        appointment["status"] = "cancelled"
        return _result(
            "success",
            "APPOINTMENT_CANCELLED",
            {"appointment": self._appointment_payload(appointment)},
        )

    def _tool_reschedule_appointment(self, args: dict[str, Any]) -> dict[str, Any]:
        appointment = self._booked(args)
        if appointment is None:
            return _result("conflict", "APPOINTMENT_ALREADY_CANCELLED")
        offered = {
            s["starts_at"]
            for s in self._slots_for_doctor_any_day(
                appointment["clinic_id"], appointment["doctor_id"], args["new_starts_at"]
            )
        }
        if args["new_starts_at"] not in offered:
            return _result("conflict", "SLOT_ALREADY_BOOKED")
        if not args.get("confirmed"):
            return _result("confirmation_required", "CONFIRM_RESCHEDULE", confirm=True)
        appointment["starts_at"] = args["new_starts_at"]
        return _result(
            "success",
            "APPOINTMENT_RESCHEDULED",
            {"appointment": self._appointment_payload(appointment)},
        )
