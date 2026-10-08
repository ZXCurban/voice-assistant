"""Patient persistence (always clinic-scoped)."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


async def get_patient(session: AsyncSession, clinic_id: int, patient_id: int) -> Patient | None:
    stmt = select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def compact_phone(phone: str) -> str:
    """Phone without the cosmetic characters people type (spaces, dashes, brackets)."""
    return phone.translate({ord(ch): None for ch in " -()\t"})


async def get_patient_by_phone(session: AsyncSession, clinic_id: int, phone: str) -> Patient | None:
    """Find a patient by phone within one clinic (voice lookup).

    An exact match wins (uses the index); otherwise the numbers are compared
    ignoring spaces, dashes and brackets, so «+48700000001» finds the patient
    registered as «+48 700 000 001».
    """
    exact = (
        select(Patient)
        .where(Patient.phone == phone.strip(), Patient.clinic_id == clinic_id)
        .order_by(Patient.id)
    )
    found = (await session.execute(exact)).scalars().first()
    if found is not None:
        return found
    stored: Any = Patient.phone
    for cosmetic in (" ", "-", "(", ")"):
        stored = func.replace(stored, cosmetic, "")
    tolerant = (
        select(Patient)
        .where(stored == compact_phone(phone), Patient.clinic_id == clinic_id)
        .order_by(Patient.id)
    )
    return (await session.execute(tolerant)).scalars().first()


async def add_patient(session: AsyncSession, patient: Patient) -> None:
    session.add(patient)
    await session.flush()
