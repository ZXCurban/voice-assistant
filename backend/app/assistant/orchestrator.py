"""AssistantOrchestrator: typed boundary for the LLM/dialogue teammate.

Orchestration only: resolve entities, delegate to existing application
services, translate errors into AssistantResult. Slot math, booking
rules, race protection and tenant isolation stay in the services layer.
No SQLAlchemy queries here beyond what services already encapsulate.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.schemas import (
    AssistantRequest,
    AssistantResult,
)
from app.core.errors import ConflictError, NotFoundError
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.specialty import Specialty
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentPatientRef,
)
from app.schemas.catalog import SpecialtyOut
from app.schemas.clinic import ClinicOut
from app.schemas.doctor import DoctorDetailOut
from app.schemas.patient import PatientCreate, PatientOut
from app.schemas.slot import SlotDoctorRef, SlotOut, SlotRoomRef, SlotSpecialtyRef
from app.services import appointments as appointments_service
from app.services import availability as availability_service
from app.services import catalog as catalog_service
from app.services import clinics as clinics_service
from app.services import doctors as doctors_service
from app.services import patients as patients_service
from app.services.common import ensure_aware_utc

_NOT_FOUND_CODES = {
    "clinic not found": "CLINIC_NOT_FOUND",
    "specialty not found": "SPECIALTY_NOT_FOUND",
    "doctor not found": "DOCTOR_NOT_FOUND",
    "patient not found": "PATIENT_NOT_FOUND",
    "appointment not found": "APPOINTMENT_NOT_FOUND",
    "room not found": "ROOM_NOT_FOUND",
    "department not found": "DEPARTMENT_NOT_FOUND",
    "no active doctors found": "NO_DOCTORS_FOR_SPECIALTY",
}

_CONFLICT_CODES = {
    "slot unavailable": "SLOT_ALREADY_BOOKED",
    "clinic is inactive": "CLINIC_INACTIVE",
    "doctor is inactive": "DOCTOR_INACTIVE",
    "room is inactive": "ROOM_INACTIVE",
    "cannot book in the past": "CANNOT_BOOK_IN_PAST",
    "cannot reschedule into the past": "CANNOT_RESCHEDULE_IN_PAST",
    "new time equals the current booking": "SAME_SLOT",
}

_APPOINTMENT_STATUSES = ("booked", "cancelled", "completed")


def _ok(code: str, message: str, details: dict[str, Any] | None = None) -> AssistantResult:
    return AssistantResult(status="success", code=code, message=message, details=details or {})


def _clarify(code: str, message: str, details: dict[str, Any] | None = None) -> AssistantResult:
    return AssistantResult(
        status="need_clarification", code=code, message=message, details=details or {}
    )


def _not_found(message: str) -> AssistantResult:
    code = _NOT_FOUND_CODES.get(message, "NOT_FOUND")
    return AssistantResult(status="not_found", code=code, message=message)


def _conflict(message: str) -> AssistantResult:
    if message.startswith("appointment already "):
        code = "APPOINTMENT_ALREADY_" + message.removeprefix("appointment already ").upper()
    else:
        code = _CONFLICT_CODES.get(message, "CONFLICT")
    return AssistantResult(status="conflict", code=code, message=message)


def _invalid(message: str) -> AssistantResult:
    return AssistantResult(status="invalid_input", code="INVALID_INPUT", message=message)


def _confirm(code: str, message: str, details: dict[str, Any] | None = None) -> AssistantResult:
    return AssistantResult(
        status="confirmation_required",
        code=code,
        message=message,
        requires_confirmation=True,
        details=details or {},
    )


def _appointment_payload(appointment: Appointment) -> dict[str, Any]:
    """Voice-ready appointment dict (mirrors api/v1/appointments.to_out)."""
    doctor = appointment.doctor
    out = AppointmentOut(
        id=appointment.id,
        clinic_id=appointment.clinic_id,
        doctor_id=appointment.doctor_id,
        patient_id=appointment.patient_id,
        room_id=appointment.room_id,
        starts_at=ensure_aware_utc(appointment.starts_at),
        ends_at=ensure_aware_utc(appointment.ends_at),
        status=appointment.status,
        reason=appointment.reason,
        created_at=ensure_aware_utc(appointment.created_at),
        updated_at=ensure_aware_utc(appointment.updated_at),
        doctor=SlotDoctorRef(id=doctor.id, full_name=doctor.full_name),
        specialty=SlotSpecialtyRef(id=doctor.specialty.id, name=doctor.specialty.name),
        patient=AppointmentPatientRef(
            id=appointment.patient.id, full_name=appointment.patient.full_name
        ),
        room=(
            SlotRoomRef(
                id=appointment.room.id,
                code=appointment.room.code,
                label=appointment.room.label,
            )
            if appointment.room is not None
            else None
        ),
    )
    return out.model_dump(mode="json")


def _doctor_candidate(doctor: Doctor) -> dict[str, Any]:
    return DoctorDetailOut.model_validate(doctor).model_dump(mode="json")


class AssistantOrchestrator:
    """Stateless facade over existing services for the AI teammate."""

    # -- shared resolution -------------------------------------------------

    def _clinic_id(self, request: AssistantRequest) -> int | None:
        if request.clinic_id is not None:
            return request.clinic_id
        if request.context is not None:
            return request.context.clinic_id
        return None

    def _patient_id(self, request: AssistantRequest) -> int | None:
        if request.patient_id is not None:
            return request.patient_id
        if request.context is not None:
            return request.context.patient_id
        return None

    async def _resolve_specialty(
        self, session: AsyncSession, clinic_id: int, request: AssistantRequest
    ) -> Specialty | AssistantResult:
        specialty_id = request.specialty_id
        if specialty_id is None and request.context is not None:
            specialty_id = request.context.selected_specialty_id
        if specialty_id is not None:
            try:
                return await catalog_service.get_specialty(session, clinic_id, specialty_id)
            except NotFoundError as exc:
                return _not_found(exc.message)
        if request.specialty_name is not None:
            wanted = request.specialty_name.strip().lower()
            for spec in await catalog_service.list_specialties(
                session, clinic_id, active_only=False
            ):
                if spec.name.strip().lower() == wanted:
                    return spec
            return _not_found("specialty not found")
        return _clarify(
            "SPECIALTY_OR_DOCTOR_REQUIRED",
            "Neither specialty nor doctor was specified.",
        )

    async def _resolve_doctor(
        self, session: AsyncSession, clinic_id: int, request: AssistantRequest
    ) -> Doctor | AssistantResult:
        doctor_id = request.doctor_id
        if doctor_id is None and request.context is not None:
            doctor_id = request.context.selected_doctor_id
        if doctor_id is not None:
            try:
                return await doctors_service.get_doctor(session, clinic_id, doctor_id)
            except NotFoundError as exc:
                return _not_found(exc.message)
        if request.doctor_name is None:
            return _clarify(
                "SPECIALTY_OR_DOCTOR_REQUIRED",
                "Neither doctor nor specialty was specified.",
            )
        wanted = request.doctor_name.strip().casefold()
        matches = [
            d
            for d in await doctors_service.list_doctors(session, clinic_id, active_only=False)
            if d.full_name.strip().casefold() == wanted
        ]
        if not matches:
            return _not_found("doctor not found")
        if len(matches) > 1:
            return _clarify(
                "AMBIGUOUS_DOCTOR",
                f"Multiple doctors named {request.doctor_name.strip()} found.",
                {"candidates": [_doctor_candidate(d) for d in matches]},
            )
        return matches[0]

    async def _resolve_slot(
        self, session: AsyncSession, clinic_id: int, doctor_id: int, wanted: datetime
    ) -> SlotOut | AssistantResult:
        """Confirm the exact instant is currently offered for the doctor."""
        clinic = await clinics_service.get_clinic(session, clinic_id)
        target = ensure_aware_utc(wanted).astimezone(ZoneInfo(clinic.timezone)).date()
        try:
            slots = await availability_service.get_doctor_slots(
                session, clinic_id, doctor_id, target
            )
        except (NotFoundError, ConflictError, ValueError) as exc:
            return self._mapped(exc)
        instant = ensure_aware_utc(wanted)
        for slot in slots:
            if ensure_aware_utc(slot.starts_at) == instant:
                return slot
        return _conflict("slot unavailable")

    def _mapped(self, exc: Exception) -> AssistantResult:
        if isinstance(exc, NotFoundError):
            return _not_found(exc.message)
        if isinstance(exc, ConflictError):
            return _conflict(exc.message)
        return _invalid(str(exc))

    # -- entry point -------------------------------------------------------

    async def handle(self, session: AsyncSession, request: AssistantRequest) -> AssistantResult:
        handler = {
            "find_clinics": self._find_clinics,
            "find_specialties": self._find_specialties,
            "find_doctors": self._find_doctors,
            "get_doctor": self._get_doctor,
            "find_slots": self._find_slots,
            "create_patient": self._create_patient,
            "get_patient": self._get_patient,
            "book_appointment": self._book_appointment,
            "get_appointment": self._get_appointment,
            "get_appointments": self._get_appointments,
            "reschedule_appointment": self._reschedule_appointment,
            "cancel_appointment": self._cancel_appointment,
            "complete_appointment": self._complete_appointment,
        }[request.intent]
        try:
            return await handler(session, request)
        except NotFoundError as exc:
            return _not_found(exc.message)
        except ConflictError as exc:
            return _conflict(exc.message)
        except ValueError as exc:
            return _invalid(str(exc))

    # -- read-only flows ---------------------------------------------------

    async def _find_clinics(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        del request
        clinics = await clinics_service.list_clinics(session, active_only=True)
        return _ok(
            "OK",
            f"Found {len(clinics)} clinics.",
            {"clinics": [ClinicOut.model_validate(c).model_dump(mode="json") for c in clinics]},
        )

    async def _find_specialties(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        specs = await catalog_service.list_specialties(session, clinic_id)
        return _ok(
            "OK",
            f"Found {len(specs)} specialties.",
            {
                "clinic_id": clinic_id,
                "specialties": [
                    SpecialtyOut.model_validate(s).model_dump(mode="json") for s in specs
                ],
            },
        )

    async def _find_doctors(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        specialty_id = request.specialty_id
        if specialty_id is None and request.specialty_name is not None:
            resolved = await self._resolve_specialty(session, clinic_id, request)
            if isinstance(resolved, AssistantResult):
                return resolved
            specialty_id = resolved.id
        doctors = await doctors_service.list_doctors(session, clinic_id, specialty_id=specialty_id)
        return _ok(
            "OK",
            f"Found {len(doctors)} doctors.",
            {
                "clinic_id": clinic_id,
                "doctors": [_doctor_candidate(d) for d in doctors],
            },
        )

    async def _get_doctor(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        doctor = await self._resolve_doctor(session, clinic_id, request)
        if isinstance(doctor, AssistantResult):
            return doctor
        return _ok("OK", doctor.full_name, {"doctor": _doctor_candidate(doctor)})

    async def _find_slots(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if request.date is None:
            return _clarify("DATE_REQUIRED", "No date was specified.")
        doctor_id = request.doctor_id
        specialty_id = request.specialty_id
        if doctor_id is None and request.doctor_name is not None:
            doctor = await self._resolve_doctor(session, clinic_id, request)
            if isinstance(doctor, AssistantResult):
                return doctor
            doctor_id = doctor.id
        elif specialty_id is None and request.specialty_name is not None:
            resolved = await self._resolve_specialty(session, clinic_id, request)
            if isinstance(resolved, AssistantResult):
                return resolved
            specialty_id = resolved.id
        if doctor_id is None and specialty_id is None and request.context is not None:
            doctor_id = request.context.selected_doctor_id
            specialty_id = request.context.selected_specialty_id
        if doctor_id is None and specialty_id is None:
            return _clarify(
                "SPECIALTY_OR_DOCTOR_REQUIRED",
                "Neither specialty nor doctor was specified.",
            )
        slots = await availability_service.search_slots(
            session, clinic_id, request.date, specialty_id=specialty_id, doctor_id=doctor_id
        )
        if not slots:
            return AssistantResult(
                status="not_found",
                code="NO_SLOTS_AVAILABLE",
                message="No available slots for the requested date.",
                details={"clinic_id": clinic_id, "slots": []},
            )
        return _ok(
            "OK",
            f"Found {len(slots)} available slots.",
            {
                "clinic_id": clinic_id,
                "slots": [s.model_dump(mode="json") for s in slots],
            },
        )

    async def _get_patient(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        patient_id = self._patient_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if patient_id is None:
            return _invalid("patient_id is required.")
        patient = await patients_service.get_patient(session, clinic_id, patient_id)
        return _ok(
            "OK",
            patient.full_name,
            {"patient": PatientOut.model_validate(patient).model_dump(mode="json")},
        )

    async def _get_appointment(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if request.appointment_id is None:
            return _invalid("appointment_id is required.")
        appointment = await appointments_service.get_appointment(
            session, clinic_id, request.appointment_id
        )
        return _ok(
            "OK",
            f"Appointment {appointment.id}.",
            {"appointment": _appointment_payload(appointment)},
        )

    async def _get_appointments(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        patient_id = self._patient_id(request)
        doctor_id = request.doctor_id
        if patient_id is None and doctor_id is None:
            return _invalid("patient_id or doctor_id is required.")
        status = request.appointment_status
        if status is not None and status not in _APPOINTMENT_STATUSES:
            return _invalid(f"Unknown status: {status}.")
        items = await appointments_service.list_appointments(
            session, clinic_id, patient_id=patient_id, doctor_id=doctor_id, status=status
        )
        return _ok(
            "OK",
            f"Found {len(items)} appointments.",
            {"appointments": [_appointment_payload(a) for a in items]},
        )

    # -- patient registration ----------------------------------------------

    async def _create_patient(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if not request.full_name or not request.phone:
            return _invalid("full_name and phone are required.")
        patient = await patients_service.create_patient(
            session,
            PatientCreate(clinic_id=clinic_id, full_name=request.full_name, phone=request.phone),
        )
        return _ok(
            "PATIENT_CREATED",
            f"Patient {patient.full_name} registered.",
            {"patient": PatientOut.model_validate(patient).model_dump(mode="json")},
        )

    # -- mutating flows (confirmation-gated) -------------------------------

    async def _book_appointment(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        patient_id = self._patient_id(request)
        if patient_id is None:
            return _clarify("PATIENT_REQUIRED", "No patient was specified.")
        doctor = await self._resolve_doctor(session, clinic_id, request)
        if isinstance(doctor, AssistantResult):
            return doctor
        if request.starts_at is None:
            if request.context is not None and request.context.selected_slot is not None:
                wanted: datetime | None = request.context.selected_slot
            else:
                return _clarify("SLOT_REQUIRED", "No slot was selected.")
        else:
            wanted = request.starts_at
        assert wanted is not None
        try:
            await patients_service.get_patient(session, clinic_id, patient_id)
        except NotFoundError as exc:
            return _not_found(exc.message)
        slot = await self._resolve_slot(session, clinic_id, doctor.id, wanted)
        if isinstance(slot, AssistantResult):
            return slot
        if not request.confirmed:
            return _confirm(
                "CONFIRM_BOOKING",
                f"Confirm booking with {doctor.full_name}.",
                {
                    "slot": slot.model_dump(mode="json"),
                    "doctor": _doctor_candidate(doctor),
                    "patient_id": patient_id,
                },
            )
        appointment = await appointments_service.book_appointment(
            session,
            AppointmentCreate(
                clinic_id=clinic_id,
                doctor_id=doctor.id,
                patient_id=patient_id,
                starts_at=ensure_aware_utc(wanted),
                reason=request.reason,
            ),
        )
        return _ok(
            "APPOINTMENT_BOOKED",
            "Appointment booked.",
            {"appointment": _appointment_payload(appointment)},
        )

    async def _reschedule_appointment(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if request.appointment_id is None:
            return _invalid("appointment_id is required.")
        if request.new_starts_at is None:
            return _clarify("NEW_SLOT_REQUIRED", "No new slot was specified.")
        current = await appointments_service.get_appointment(
            session, clinic_id, request.appointment_id
        )
        if current.status != "booked":
            return _conflict(f"appointment already {current.status}")
        slot = await self._resolve_slot(
            session, clinic_id, current.doctor_id, request.new_starts_at
        )
        if isinstance(slot, AssistantResult):
            return slot
        if not request.confirmed:
            return _confirm(
                "CONFIRM_RESCHEDULE",
                "Confirm moving the appointment.",
                {
                    "appointment": _appointment_payload(current),
                    "new_slot": slot.model_dump(mode="json"),
                },
            )
        moved = await appointments_service.reschedule_appointment(
            session, clinic_id, request.appointment_id, request.new_starts_at
        )
        return _ok(
            "APPOINTMENT_RESCHEDULED",
            "Appointment rescheduled.",
            {"appointment": _appointment_payload(moved)},
        )

    async def _cancel_appointment(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if request.appointment_id is None:
            return _invalid("appointment_id is required.")
        current = await appointments_service.get_appointment(
            session, clinic_id, request.appointment_id
        )
        if current.status != "booked":
            return _conflict(f"appointment already {current.status}")
        if not request.confirmed:
            return _confirm(
                "CONFIRM_CANCEL",
                "Confirm cancelling the appointment.",
                {"appointment": _appointment_payload(current)},
            )
        cancelled = await appointments_service.cancel_appointment(
            session, clinic_id, request.appointment_id
        )
        return _ok(
            "APPOINTMENT_CANCELLED",
            "Appointment cancelled.",
            {"appointment": _appointment_payload(cancelled)},
        )

    async def _complete_appointment(
        self, session: AsyncSession, request: AssistantRequest
    ) -> AssistantResult:
        clinic_id = self._clinic_id(request)
        if clinic_id is None:
            return _clarify("CLINIC_REQUIRED", "No clinic was specified.")
        if request.appointment_id is None:
            return _invalid("appointment_id is required.")
        current = await appointments_service.get_appointment(
            session, clinic_id, request.appointment_id
        )
        if current.status != "booked":
            return _conflict(f"appointment already {current.status}")
        if not request.confirmed:
            return _confirm(
                "CONFIRM_COMPLETE",
                "Confirm completing the appointment.",
                {"appointment": _appointment_payload(current)},
            )
        done = await appointments_service.complete_appointment(
            session, clinic_id, request.appointment_id
        )
        return _ok(
            "APPOINTMENT_COMPLETED",
            "Appointment completed.",
            {"appointment": _appointment_payload(done)},
        )
