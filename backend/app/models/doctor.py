"""Doctor model — one primary specialty, one nullable department (D4)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.clinic import Clinic
    from app.models.department import Department
    from app.models.doctor_schedule import DoctorSchedule
    from app.models.specialty import Specialty


class Doctor(Base):
    """Doctor employed by exactly one clinic."""

    __tablename__ = "doctors"
    __table_args__ = (
        Index("ix_doctors_clinic_specialty_active", "clinic_id", "specialty_id", "active"),
        Index("ix_doctors_clinic_department", "clinic_id", "department_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    specialty_id: Mapped[int] = mapped_column(
        ForeignKey("specialties.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship(back_populates="doctors")
    specialty: Mapped["Specialty"] = relationship(back_populates="doctors")
    department: Mapped["Department | None"] = relationship(back_populates="doctors")
    schedules: Mapped[list["DoctorSchedule"]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")
