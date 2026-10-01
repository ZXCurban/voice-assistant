"""Clinic persistence (tenant root — the only unscoped aggregate)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinic import Clinic


async def get_clinic(session: AsyncSession, clinic_id: int) -> Clinic | None:
    return await session.get(Clinic, clinic_id)


async def list_clinics(
    session: AsyncSession, *, active_only: bool = True, limit: int = 100, offset: int = 0
) -> list[Clinic]:
    stmt = select(Clinic).order_by(Clinic.id).offset(offset).limit(limit)
    if active_only:
        stmt = stmt.where(Clinic.active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_clinic(session: AsyncSession, clinic: Clinic) -> Clinic:
    session.add(clinic)
    await session.flush()
    return clinic
