"""Room model — physical room, informational for MVP (D5)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.department import Department


class Room(Base):
    """E.g. A-101. Code unique within a clinic, not globally.

    Rooms are informational (answer "where") — MVP performs no
    room-capacity conflict checks.
    """

    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("clinic_id", "code", name="uq_rooms_clinic_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    floor: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    clinic: Mapped["Clinic"] = relationship(back_populates="rooms")
    department: Mapped["Department | None"] = relationship(back_populates="rooms")
