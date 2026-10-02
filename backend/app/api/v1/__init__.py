"""API router aggregation.

GET /health stays at the root as a stable contract. All domain endpoints
live under /api/v1 (patient-facing) and /api/v1/management.
"""

from fastapi import APIRouter

from app.api.v1 import appointments, catalog, chat, clinics, patients, slots
from app.api.v1.health import router as health_router
from app.api.v1.management import management_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(clinics.router)
api_router.include_router(catalog.router)
api_router.include_router(patients.router)
api_router.include_router(slots.router)
api_router.include_router(appointments.router)
api_router.include_router(management_router)
api_router.include_router(chat.router)
