"""Available-slot response schemas (voice-assistant facing)."""

from datetime import datetime

from pydantic import BaseModel


class SlotDoctorRef(BaseModel):
    id: int
    full_name: str


class SlotSpecialtyRef(BaseModel):
    id: int
    name: str


class SlotRoomRef(BaseModel):
    id: int
    code: str
    label: str | None = None


class SlotOut(BaseModel):
    doctor: SlotDoctorRef
    specialty: SlotSpecialtyRef
    starts_at: datetime
    ends_at: datetime
    room: SlotRoomRef | None = None
