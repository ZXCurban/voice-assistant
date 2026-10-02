"""Clinic model — the tenant root of the multi-clinic platform."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.department import Department
    from app.models.doctor import Doctor
    from app.models.patient import Patient
    from app.models.room import Room
    from app.models.specialty import Specialty


class Clinic(Base):
    """Independent clinic (tenant). All business data hangs off clinic_id."""

    __tablename__ = "clinics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Optional geo for "nearest clinic" ranking (see services/geo.py).
    # All nullable: old rows and old API payloads keep working.
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # IANA timezone name, e.g. "Europe/Warsaw". Mandatory: schedules are
    # interpreted in clinic-local time, appointments stored as UTC.
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    specialties: Mapped[list["Specialty"]] = relationship(back_populates="clinic")
    departments: Mapped[list["Department"]] = relationship(back_populates="clinic")
    rooms: Mapped[list["Room"]] = relationship(back_populates="clinic")
    doctors: Mapped[list["Doctor"]] = relationship(back_populates="clinic")
    patients: Mapped[list["Patient"]] = relationship(back_populates="clinic")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="clinic")
