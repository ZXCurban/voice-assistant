"""Availability endpoints — the core voice-assistant capability."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.slot import SlotOut
from app.services import availability as availability_service

router = APIRouter(prefix="/api/v1", tags=["slots"])


@router.get(
    "/slots",
    response_model=list[SlotOut],
    summary="Search available slots",
    description=(
        "Voice-assistant entry point for 'I need a dermatologist tomorrow'. "
        "Pass clinic_id + date (YYYY-MM-DD, clinic-local) + exactly one of "
        "specialty_id or doctor_id. Returns UTC starts_at/ends_at pairs with "
        "doctor, specialty and room context. Empty list = fully booked or "
        "closed (check clinic schedule / exceptions)."
    ),
)
async def search_slots(
    session: SessionDep,
    clinic_id: Annotated[int, Query(gt=0)],
    target_date: Annotated[date, Query(alias="date")],
    specialty_id: Annotated[int | None, Query(gt=0)] = None,
    doctor_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SlotOut]:
    return await availability_service.search_slots(
        session,
        clinic_id,
        target_date,
        specialty_id=specialty_id,
        doctor_id=doctor_id,
        limit=limit,
    )


@router.get(
    "/doctors/{doctor_id}/slots",
    response_model=list[SlotOut],
    summary="Available slots of one doctor on a date",
    description=(
        "Day grid for one doctor after clinic hours, exceptions and booked "
        "visits are applied. Use after the patient picks a doctor."
    ),
)
async def doctor_slots(
    session: SessionDep,
    doctor_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
    target_date: Annotated[date, Query(alias="date")],
) -> list[SlotOut]:
    return await availability_service.get_doctor_slots(session, clinic_id, doctor_id, target_date)
