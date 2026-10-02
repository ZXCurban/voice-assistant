"""Patient persistence (always clinic-scoped)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


async def get_patient(session: AsyncSession, clinic_id: int, patient_id: int) -> Patient | None:
    stmt = select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_patient_by_phone(session: AsyncSession, clinic_id: int, phone: str) -> Patient | None:
    """Find a patient by exact phone match within one clinic (voice lookup)."""
    stmt = select(Patient).where(Patient.phone == phone.strip(), Patient.clinic_id == clinic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def add_patient(session: AsyncSession, patient: Patient) -> None:
    session.add(patient)
    await session.flush()
