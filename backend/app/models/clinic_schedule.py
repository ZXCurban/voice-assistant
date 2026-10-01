"""ClinicSchedule model — weekly clinic opening hours (clinic-local time)."""

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
    from app.models.clinic import Clinic


class ClinicSchedule(Base):
    """One opening interval for a weekday, e.g. Monday 08:00-20:00.

    Multiple rows per weekday allowed (split shifts). Absence of rows
    for a weekday means the clinic is closed that day.
    """

    __tablename__ = "clinic_schedules"
    __table_args__ = (
        CheckConstraint("start_local < end_local", name="ck_clinic_schedules_interval"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_clinic_schedules_weekday"),
        Index("ix_clinic_schedules_clinic_weekday", "clinic_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    # 0 = Monday .. 6 = Sunday (Python weekday() convention).
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_local: Mapped[time] = mapped_column(Time, nullable=False)
    end_local: Mapped[time] = mapped_column(Time, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship()
