"""Clinic-management routers (admin panel / seed scripts)."""

from fastapi import APIRouter

from app.api.v1.management import catalog, clinics, doctors, exceptions

management_router = APIRouter()
management_router.include_router(clinics.router)
management_router.include_router(catalog.router)
management_router.include_router(doctors.router)
management_router.include_router(exceptions.router)

__all__ = ["management_router"]
