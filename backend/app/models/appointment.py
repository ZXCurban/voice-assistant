"""Appointment model — concrete UTC booking with race protection."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.doctor import Doctor
    from app.models.patient import Patient
    from app.models.room import Room

STATUS_BOOKED = "booked"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
APPOINTMENT_STATUSES = (STATUS_BOOKED, STATUS_CANCELLED, STATUS_COMPLETED)


class Appointment(Base):
    """Booked visit. starts_at/ends_at stored as UTC TIMESTAMPTZ.

    Double-booking is prevented by the partial unique index
    uq_appointments_booked_slot: only 'booked' rows participate, so a
    concurrent second INSERT for the same (clinic, doctor, instant) fails
    and cancelling frees the instant for re-booking.
    """

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_appointments_interval"),
        CheckConstraint(
            "status IN ('booked', 'cancelled', 'completed')",
            name="ck_appointments_status",
        ),
        Index(
            "uq_appointments_booked_slot",
            "clinic_id",
            "doctor_id",
            "starts_at",
            unique=True,
            postgresql_where=text("status = 'booked'"),
            sqlite_where=text("status = 'booked'"),
        ),
        Index("ix_appointments_doctor_starts", "doctor_id", "starts_at"),
        Index("ix_appointments_patient_starts", "patient_id", "starts_at"),
        Index("ix_appointments_clinic_starts", "clinic_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_BOOKED)
    # Patient-stated visit purpose, non-clinical, optional (D6).
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship(back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship(back_populates="appointments")
    patient: Mapped["Patient"] = relationship(back_populates="appointments")
    room: Mapped["Room | None"] = relationship()
