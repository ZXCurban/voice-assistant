"""DoctorSchedule model — weekly doctor availability (clinic-local time)."""

from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.doctor import Doctor
    from app.models.room import Room

ALLOWED_SLOT_MINUTES = (5, 10, 15, 20, 30, 45, 60)


class DoctorSchedule(Base):
    """One working interval for a doctor's weekday, e.g. Monday 09:00-13:00.

    slot_minutes fixes the booking grid for this interval (D8).
    room_id is the doctor's usual room for this interval (informational).
    """

    __tablename__ = "doctor_schedules"
    __table_args__ = (
        CheckConstraint("start_local < end_local", name="ck_doctor_schedules_interval"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_doctor_schedules_weekday"),
        CheckConstraint(
            "slot_minutes IN (5, 10, 15, 20, 30, 45, 60)",
            name="ck_doctor_schedules_slot",
        ),
        Index("ix_doctor_schedules_doctor_weekday", "doctor_id", "weekday"),
        Index("ix_doctor_schedules_clinic_doctor", "clinic_id", "doctor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_local: Mapped[time] = mapped_column(Time, nullable=False)
    end_local: Mapped[time] = mapped_column(Time, nullable=False)
    slot_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=20)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    doctor: Mapped["Doctor"] = relationship(back_populates="schedules")
    room: Mapped["Room | None"] = relationship()
