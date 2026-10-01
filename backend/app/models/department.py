"""Department model — clinic-owned organizational unit."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.doctor import Doctor
    from app.models.room import Room


class Department(Base):
    """E.g. Internal Medicine, Diagnostics. Owned by exactly one clinic."""

    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_departments_clinic_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    floor: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship(back_populates="departments")
    rooms: Mapped[list["Room"]] = relationship(back_populates="department")
    doctors: Mapped[list["Doctor"]] = relationship(back_populates="department")
