"""Patient/assistant-facing clinic discovery endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.clinic import ClinicOut
from app.services import clinics as clinics_service

router = APIRouter(prefix="/api/v1", tags=["clinics"])


@router.get("/clinics", response_model=list[ClinicOut], summary="List active clinics")
async def list_clinics(
    session: SessionDep,
    active_only: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ClinicOut]:
    entities = await clinics_service.list_clinics(
        session, active_only=active_only, limit=limit, offset=offset
    )
    return [ClinicOut.model_validate(e) for e in entities]


@router.get("/clinics/{clinic_id}", response_model=ClinicOut, summary="Get clinic details")
async def get_clinic(session: SessionDep, clinic_id: int) -> ClinicOut:
    entity = await clinics_service.get_public_clinic(session, clinic_id)
    return ClinicOut.model_validate(entity)
