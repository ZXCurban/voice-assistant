"""Patient registration + profile use-cases."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.patient import Patient
from app.repositories import patients as patients_repo
from app.schemas.patient import PatientCreate, PatientUpdate
from app.services.common import require_clinic
from app.services.common import save_or_conflict as _save


def _validate_birth_date(value: date) -> None:
    if value > date.today():
        raise ValueError("birth_date must not be in the future")


async def create_patient(session: AsyncSession, data: PatientCreate) -> Patient:
    clinic = await require_clinic(session, data.clinic_id)
    if not clinic.active:
        raise ConflictError("clinic is inactive")
    if data.birth_date is not None:
        _validate_birth_date(data.birth_date)
    patient = Patient(
        clinic_id=data.clinic_id,
        full_name=data.full_name.strip(),
        phone=data.phone.strip(),
        birth_date=data.birth_date,
    )
    session.add(patient)
    await _save(session, "could not create patient")
    await session.refresh(patient)
    return patient


async def get_patient(session: AsyncSession, clinic_id: int, patient_id: int) -> Patient:
    patient = await patients_repo.get_patient(session, clinic_id, patient_id)
    if patient is None:
        raise NotFoundError("patient not found")
    return patient


async def update_patient(
    session: AsyncSession, clinic_id: int, patient_id: int, data: PatientUpdate
) -> Patient:
    patient = await get_patient(session, clinic_id, patient_id)
    if data.full_name is not None:
        patient.full_name = data.full_name.strip()
    if data.phone is not None:
        patient.phone = data.phone.strip()
    if data.birth_date is not None:
        _validate_birth_date(data.birth_date)
        patient.birth_date = data.birth_date
    await _save(session, "could not update patient")
    await session.refresh(patient)
    return patient
