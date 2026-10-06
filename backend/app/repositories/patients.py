"""Patient persistence (always clinic-scoped)."""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


async def get_patient(session: AsyncSession, clinic_id: int, patient_id: int) -> Patient | None:
    stmt = select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_patient_by_phone(session: AsyncSession, clinic_id: int, phone: str) -> Patient | None:
    """Find by phone digits within the clinic, independent of spoken formatting."""
    stored_digits = func.replace(
        func.replace(
            func.replace(func.replace(func.replace(Patient.phone, " ", ""), "-", ""), "+", ""),
            "(",
            "",
        ),
        ")",
        "",
    )
    requested_digits = re.sub(r"\D", "", phone)
    stmt = select(Patient).where(
        stored_digits == requested_digits,
        Patient.clinic_id == clinic_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_patients(session: AsyncSession, clinic_id: int, *, limit: int = 2) -> list[Patient]:
    """Clinic-scoped lookup used only to auto-resolve a sole demo patient."""
    stmt = select(Patient).where(Patient.clinic_id == clinic_id).order_by(Patient.id).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def add_patient(session: AsyncSession, patient: Patient) -> None:
    session.add(patient)
    await session.flush()
