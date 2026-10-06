"""Stateful assistant bookings must execute through the real backend services."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.dialogue import DialogueState
from app.assistant.dialogue_manager import DialogueManager
from app.assistant.orchestrator import AssistantOrchestrator
from app.models.appointment import Appointment
from tests.integration.helpers import future_monday, make_clinic


async def test_dialogue_booking_creates_exactly_one_real_appointment(
    db_session: AsyncSession,
) -> None:
    refs = await make_clinic(db_session, name="Dialogue Booking Clinic")
    manager = DialogueManager(AssistantOrchestrator())
    state = DialogueState(
        conversation_id="real-dialogue-booking",
        clinic_id=refs["clinic_id"],
        patient_id=refs["patient_id"],
    )

    date_prompt = await manager.handle(db_session, state, "Запишите меня к кардиологу")
    assert "дату" in date_prompt.casefold()

    target_date: date = future_monday()
    options = await manager.handle(
        db_session, state, f"{target_date.day:02d}.{target_date.month:02d}.{target_date.year}"
    )
    assert "Доступное время" in options
    assert state.available_slots

    confirmation = await manager.handle(db_session, state, "Первый")
    assert "Скажите «да» или «нет»" in confirmation
    assert state.phase == "CONFIRMING"

    booked = await manager.handle(db_session, state, "Да")
    assert "запись оформлена" in booked.casefold()
    assert state.phase == "COMPLETED"

    duplicate_confirmation = await manager.handle(db_session, state, "Да")
    assert duplicate_confirmation == booked
    count = await db_session.scalar(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.clinic_id == refs["clinic_id"],
            Appointment.patient_id == refs["patient_id"],
            Appointment.status == "booked",
        )
    )
    assert count == 1
