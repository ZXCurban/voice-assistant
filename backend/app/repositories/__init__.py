"""Thin persistence layer: typed queries, always scoped by clinic_id."""

from app.repositories import (
    appointments,
    catalog,
    clinics,
    doctors,
    exceptions,
    patients,
    schedules,
)

__all__ = [
    "appointments",
    "catalog",
    "clinics",
    "doctors",
    "exceptions",
    "patients",
    "schedules",
]
