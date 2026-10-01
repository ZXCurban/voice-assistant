"""Doctor management + patient-facing doctor queries."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.doctor import Doctor
from app.repositories import doctors as doctors_repo
from app.schemas.doctor import DoctorCreate, DoctorUpdate
from app.services import catalog as catalog_service
from app.services.common import require_clinic
from app.services.common import save_or_conflict as _save


async def create_doctor(session: AsyncSession, data: DoctorCreate) -> Doctor:
    await require_clinic(session, data.clinic_id)
    # Scope checks: specialty/department must belong to the same clinic.
    await catalog_service.get_specialty(session, data.clinic_id, data.specialty_id)
    if data.department_id is not None:
        await catalog_service.get_department(session, data.clinic_id, data.department_id)
    doctor = Doctor(
        clinic_id=data.clinic_id,
        full_name=data.full_name.strip(),
        specialty_id=data.specialty_id,
        department_id=data.department_id,
        phone=data.phone,
        active=True,
    )
    session.add(doctor)
    await _save(session, "could not create doctor")
    await session.refresh(doctor)
    return doctor


async def get_doctor(session: AsyncSession, clinic_id: int, doctor_id: int) -> Doctor:
    doctor = await doctors_repo.get_doctor_detail(session, clinic_id, doctor_id)
    if doctor is None:
        raise NotFoundError("doctor not found")
    return doctor


async def update_doctor(
    session: AsyncSession, clinic_id: int, doctor_id: int, data: DoctorUpdate
) -> Doctor:
    doctor = await get_doctor(session, clinic_id, doctor_id)
    if data.specialty_id is not None:
        await catalog_service.get_specialty(session, clinic_id, data.specialty_id)
        doctor.specialty_id = data.specialty_id
    if data.department_id is not None:
        await catalog_service.get_department(session, clinic_id, data.department_id)
        doctor.department_id = data.department_id
    if data.full_name is not None:
        doctor.full_name = data.full_name.strip()
    if data.phone is not None:
        doctor.phone = data.phone
    if data.active is not None:
        doctor.active = data.active
    await _save(session, "could not update doctor")
    await session.refresh(doctor)
    return doctor


async def list_doctors(
    session: AsyncSession,
    clinic_id: int,
    *,
    specialty_id: int | None = None,
    department_id: int | None = None,
    active_only: bool = True,
) -> list[Doctor]:
    await require_clinic(session, clinic_id)
    if specialty_id is not None:
        await catalog_service.get_specialty(session, clinic_id, specialty_id)
    if department_id is not None:
        await catalog_service.get_department(session, clinic_id, department_id)
    return await doctors_repo.list_doctors(
        session,
        clinic_id,
        specialty_id=specialty_id,
        department_id=department_id,
        active_only=active_only,
    )
