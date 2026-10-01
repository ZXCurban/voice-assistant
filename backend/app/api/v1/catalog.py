"""Patient/assistant-facing catalog: specialties, departments, rooms, doctors."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.catalog import DepartmentOut, RoomOut, SpecialtyOut
from app.schemas.doctor import DoctorDetailOut
from app.services import catalog as catalog_service
from app.services import doctors as doctors_service

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get(
    "/clinics/{clinic_id}/specialties",
    response_model=list[SpecialtyOut],
    summary="List clinic specialties",
)
async def list_specialties(
    session: SessionDep,
    clinic_id: int,
    active_only: Annotated[bool, Query()] = True,
) -> list[SpecialtyOut]:
    entities = await catalog_service.list_specialties(session, clinic_id, active_only=active_only)
    return [SpecialtyOut.model_validate(e) for e in entities]


@router.get(
    "/clinics/{clinic_id}/departments",
    response_model=list[DepartmentOut],
    summary="List clinic departments",
)
async def list_departments(
    session: SessionDep,
    clinic_id: int,
    active_only: Annotated[bool, Query()] = True,
) -> list[DepartmentOut]:
    entities = await catalog_service.list_departments(session, clinic_id, active_only=active_only)
    return [DepartmentOut.model_validate(e) for e in entities]


@router.get(
    "/clinics/{clinic_id}/rooms",
    response_model=list[RoomOut],
    summary="List clinic rooms",
)
async def list_rooms(
    session: SessionDep,
    clinic_id: int,
    department_id: Annotated[int | None, Query(gt=0)] = None,
    active_only: Annotated[bool, Query()] = True,
) -> list[RoomOut]:
    entities = await catalog_service.list_rooms(
        session, clinic_id, department_id=department_id, active_only=active_only
    )
    return [RoomOut.model_validate(e) for e in entities]


@router.get(
    "/clinics/{clinic_id}/doctors",
    response_model=list[DoctorDetailOut],
    summary="List clinic doctors",
    description=(
        "Doctors with nested specialty/department names so a voice "
        "assistant can disambiguate same-name doctors without extra calls."
    ),
)
async def list_doctors(
    session: SessionDep,
    clinic_id: int,
    specialty_id: Annotated[int | None, Query(gt=0)] = None,
    department_id: Annotated[int | None, Query(gt=0)] = None,
    active_only: Annotated[bool, Query()] = True,
) -> list[DoctorDetailOut]:
    entities = await doctors_service.list_doctors(
        session,
        clinic_id,
        specialty_id=specialty_id,
        department_id=department_id,
        active_only=active_only,
    )
    return [DoctorDetailOut.model_validate(e) for e in entities]


@router.get(
    "/doctors/{doctor_id}",
    response_model=DoctorDetailOut,
    summary="Get doctor with specialty/department",
    description=(
        "Answers 'where does Dr. X see patients': department plus the "
        "rooms/slots endpoints complete the picture. Always pass the "
        "clinic_id the patient chose; wrong scope returns 404."
    ),
)
async def get_doctor(
    session: SessionDep,
    doctor_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DoctorDetailOut:
    doctor = await doctors_service.get_doctor(session, clinic_id, doctor_id)
    return DoctorDetailOut.model_validate(doctor)
