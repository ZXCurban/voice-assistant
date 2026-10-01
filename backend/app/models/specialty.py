"""Specialty model — clinic-owned medical specialty catalog."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.doctor import Doctor


class Specialty(Base):
    """E.g. Cardiology, Dermatology. Owned by exactly one clinic (D2)."""

    __tablename__ = "specialties"
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_specialties_clinic_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship(back_populates="specialties")
    doctors: Mapped[list["Doctor"]] = relationship(back_populates="specialty")
