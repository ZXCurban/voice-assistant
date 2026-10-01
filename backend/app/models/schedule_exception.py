"""ScheduleException model — date-specific overrides (single table, D7)."""

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Time,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.doctor import Doctor

EXCEPTION_DAY_OFF = "day_off"
EXCEPTION_CUSTOM_HOURS = "custom_hours"


class ScheduleException(Base):
    """Date override for a clinic (doctor_id NULL) or one doctor.

    kind='day_off': no slots that date (times must be NULL).
    kind='custom_hours': replaces weekly intervals for that date.
    """

    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        CheckConstraint("kind IN ('day_off', 'custom_hours')", name="ck_exceptions_kind"),
        CheckConstraint(
            "(kind = 'day_off' AND start_local IS NULL AND end_local IS NULL) OR "
            "(kind = 'custom_hours' AND start_local IS NOT NULL AND "
            "end_local IS NOT NULL AND start_local < end_local)",
            name="ck_exceptions_times",
        ),
        # One clinic-wide row per date; one per (doctor, date). Partial
        # indexes keep NULL doctor_ids from collapsing into one conflict.
        Index(
            "uq_exceptions_clinic_date",
            "clinic_id",
            "date",
            unique=True,
            postgresql_where=text("doctor_id IS NULL"),
            sqlite_where=text("doctor_id IS NULL"),
        ),
        Index(
            "uq_exceptions_clinic_doctor_date",
            "clinic_id",
            "doctor_id",
            "date",
            unique=True,
            postgresql_where=text("doctor_id IS NOT NULL"),
            sqlite_where=text("doctor_id IS NOT NULL"),
        ),
        Index("ix_exceptions_doctor_date", "doctor_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), nullable=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    start_local: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_local: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    doctor: Mapped["Doctor | None"] = relationship()
