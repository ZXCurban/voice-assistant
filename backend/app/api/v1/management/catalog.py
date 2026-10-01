"""Specialty / department / room management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.schemas.catalog import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    SpecialtyCreate,
    SpecialtyOut,
    SpecialtyUpdate,
)
from app.services import catalog as catalog_service

router = APIRouter(prefix="/api/v1/management", tags=["management-catalog"])


@router.post(
    "/specialties",
    response_model=SpecialtyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a specialty",
)
async def create_specialty(session: SessionDep, data: SpecialtyCreate) -> SpecialtyOut:
    return SpecialtyOut.model_validate(await catalog_service.create_specialty(session, data))


@router.get(
    "/specialties/{specialty_id}",
    response_model=SpecialtyOut,
    summary="Get a specialty",
)
async def get_specialty(
    session: SessionDep,
    specialty_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> SpecialtyOut:
    return SpecialtyOut.model_validate(
        await catalog_service.get_specialty(session, clinic_id, specialty_id)
    )


@router.patch(
    "/specialties/{specialty_id}",
    response_model=SpecialtyOut,
    summary="Update a specialty",
)
async def update_specialty(
    session: SessionDep,
    specialty_id: int,
    data: SpecialtyUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> SpecialtyOut:
    return SpecialtyOut.model_validate(
        await catalog_service.update_specialty(session, clinic_id, specialty_id, data)
    )


@router.post(
    "/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a department",
)
async def create_department(session: SessionDep, data: DepartmentCreate) -> DepartmentOut:
    return DepartmentOut.model_validate(await catalog_service.create_department(session, data))


@router.get(
    "/departments/{department_id}",
    response_model=DepartmentOut,
    summary="Get a department",
)
async def get_department(
    session: SessionDep,
    department_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DepartmentOut:
    return DepartmentOut.model_validate(
        await catalog_service.get_department(session, clinic_id, department_id)
    )


@router.patch(
    "/departments/{department_id}",
    response_model=DepartmentOut,
    summary="Update a department",
)
async def update_department(
    session: SessionDep,
    department_id: int,
    data: DepartmentUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> DepartmentOut:
    return DepartmentOut.model_validate(
        await catalog_service.update_department(session, clinic_id, department_id, data)
    )


@router.post(
    "/rooms",
    response_model=RoomOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a room",
)
async def create_room(session: SessionDep, data: RoomCreate) -> RoomOut:
    return RoomOut.model_validate(await catalog_service.create_room(session, data))


@router.get("/rooms/{room_id}", response_model=RoomOut, summary="Get a room")
async def get_room(
    session: SessionDep,
    room_id: int,
    clinic_id: Annotated[int, Query(gt=0)],
) -> RoomOut:
    return RoomOut.model_validate(await catalog_service.get_room(session, clinic_id, room_id))


@router.patch("/rooms/{room_id}", response_model=RoomOut, summary="Update a room")
async def update_room(
    session: SessionDep,
    room_id: int,
    data: RoomUpdate,
    clinic_id: Annotated[int, Query(gt=0)],
) -> RoomOut:
    return RoomOut.model_validate(
        await catalog_service.update_room(session, clinic_id, room_id, data)
    )
