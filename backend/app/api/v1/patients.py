"""Patient/assistant-facing patient registration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.patient import PatientCreate, PatientOut, PatientUpdate
from app.services import patients as patients_service

router = APIRouter(prefix="/api/v1", tags=["patients"])


@router.post(
    "/patients",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register patient in a clinic",
    description=(
        "Creates a per-clinic patient profile (no medical data). "
        "The same person at another clinic is a separate record — "
        "always use the clinic_id of the chosen clinic."
    ),
)
async def create_patient(session: SessionDep, data: PatientCreate) -> PatientOut:
    patient = await patients_service.create_patient(session, data)
    return PatientOut.model_validate(patient)


@router.get("/patients/{patient_id}", response_model=PatientOut, summary="Get patient profile")
async def get_patient(
    session: SessionDep,
    patient_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> PatientOut:
    patient = await patients_service.get_patient(session, clinic_id, patient_id)
    return PatientOut.model_validate(patient)


@router.patch(
    "/patients/{patient_id}",
    response_model=PatientOut,
    summary="Correct patient profile",
)
async def update_patient(
    session: SessionDep,
    patient_id: int,
    data: PatientUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> PatientOut:
    patient = await patients_service.update_patient(session, clinic_id, patient_id, data)
    return PatientOut.model_validate(patient)
