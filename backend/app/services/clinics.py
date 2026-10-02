"""Clinic management use-cases."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.clinic import Clinic
from app.repositories import clinics as clinics_repo
from app.schemas.clinic import ClinicCreate, ClinicUpdate
from app.services.common import flush_or_conflict, require_clinic, validate_timezone


async def create_clinic(session: AsyncSession, data: ClinicCreate) -> Clinic:
    clinic = Clinic(
        name=data.name.strip(),
        description=data.description,
        address=data.address,
        city=data.city,
        latitude=data.latitude,
        longitude=data.longitude,
        phone=data.phone,
        timezone=validate_timezone(data.timezone),
        active=True,
    )
    session.add(clinic)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("clinic name already exists") from exc
    await session.commit()
    await session.refresh(clinic)
    return clinic


async def get_clinic(session: AsyncSession, clinic_id: int) -> Clinic:
    return await require_clinic(session, clinic_id)


async def list_clinics(
    session: AsyncSession, *, active_only: bool = True, limit: int = 100, offset: int = 0
) -> list[Clinic]:
    return await clinics_repo.list_clinics(
        session, active_only=active_only, limit=limit, offset=offset
    )


async def update_clinic(session: AsyncSession, clinic_id: int, data: ClinicUpdate) -> Clinic:
    clinic = await require_clinic(session, clinic_id)
    if data.name is not None:
        clinic.name = data.name.strip()
    if data.description is not None:
        clinic.description = data.description
    if data.address is not None:
        clinic.address = data.address
    if data.city is not None:
        clinic.city = data.city
    if data.latitude is not None:
        clinic.latitude = data.latitude
    if data.longitude is not None:
        clinic.longitude = data.longitude
    if data.phone is not None:
        clinic.phone = data.phone
    if data.timezone is not None:
        clinic.timezone = validate_timezone(data.timezone)
    if data.active is not None:
        clinic.active = data.active
    await flush_or_conflict(session, "clinic name already exists")
    await session.commit()
    await session.refresh(clinic)
    return clinic


async def get_public_clinic(session: AsyncSession, clinic_id: int) -> Clinic:
    """Patient-facing fetch; missing → 404 (same as management)."""
    clinic = await clinics_repo.get_clinic(session, clinic_id)
    if clinic is None:
        raise NotFoundError("clinic not found")
    return clinic
