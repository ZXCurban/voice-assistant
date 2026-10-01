"""Deterministic assistant demo against real PostgreSQL (no mocks).

Requires migrated + seeded database. Run from backend/:

    DATABASE_URL="postgresql+asyncpg://.../app" python -m app.assistant.demo

Flow: find_slots (dermatology) -> book preview (confirmation_required)
-> confirmed booking -> confirmed cancellation.
"""

import asyncio
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import AssistantRequest
from app.db.session import get_engine
from app.models.patient import Patient
from app.schemas.patient import PatientCreate
from app.services import patients as patients_service

orchestrator = AssistantOrchestrator()


def _show(label: str, result: object) -> None:
    from app.assistant.schemas import AssistantResult

    assert isinstance(result, AssistantResult)
    print(f"[{result.status}/{result.code}] {label}: {result.message}")


async def main() -> None:
    engine = get_engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        clinics = await orchestrator.handle(session, AssistantRequest(intent="find_clinics"))
        _show("find_clinics", clinics)
        clinic = next(
            c for c in clinics.details["clinics"] if c["name"] == "Przychodnia Srodmiescie"
        )
        clinic_id: int = clinic["id"]

        target = None
        slots: list[dict[str, Any]] = []
        for ahead in range(1, 15):
            day: date = date.today() + timedelta(days=ahead)
            found = await orchestrator.handle(
                session,
                AssistantRequest(
                    intent="find_slots",
                    clinic_id=clinic_id,
                    specialty_name="Dermatology",
                    date=day,
                ),
            )
            if found.status == "success" and found.details["slots"]:
                target, slots = day, found.details["slots"]
                break
        assert target is not None and slots, "seed has no dermatology slots?"
        first_slot = slots[0]
        first_instant = str(first_slot["starts_at"])
        assert isinstance(first_slot["doctor"], dict)
        first_doctor_id = int(first_slot["doctor"]["id"])
        print(f"slots on {target}: {[s['starts_at'] for s in slots[:3]]}")

        existing = (
            await session.execute(
                select(Patient).where(
                    Patient.clinic_id == clinic_id, Patient.phone == "+48000000009"
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            patient = await patients_service.create_patient(
                session,
                PatientCreate(
                    clinic_id=clinic_id,
                    full_name="Assistant Demo",
                    phone="+48000000009",
                ),
            )
            patient_id = patient.id
        else:
            patient_id = existing.id
        print(f"patient_id={patient_id}")

        preview = await orchestrator.handle(
            session,
            AssistantRequest(
                intent="book_appointment",
                clinic_id=clinic_id,
                patient_id=patient_id,
                doctor_id=first_doctor_id,
                starts_at=first_instant,  # type: ignore[arg-type]
            ),
        )
        _show("book preview", preview)
        assert preview.status == "confirmation_required"
        assert preview.requires_confirmation is True

        booked = await orchestrator.handle(
            session,
            AssistantRequest(
                intent="book_appointment",
                clinic_id=clinic_id,
                patient_id=patient_id,
                doctor_id=first_doctor_id,
                starts_at=first_instant,  # type: ignore[arg-type]
                confirmed=True,
            ),
        )
        _show("book confirmed", booked)
        appointment_id: int = booked.details["appointment"]["id"]

        cancelled = await orchestrator.handle(
            session,
            AssistantRequest(
                intent="cancel_appointment",
                clinic_id=clinic_id,
                appointment_id=appointment_id,
                confirmed=True,
            ),
        )
        _show("cancel confirmed", cancelled)
        print("\nAssistant demo OK.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
