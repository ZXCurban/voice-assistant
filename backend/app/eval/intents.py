"""Canonical assistant intent vocabulary for eval datasets and harnesses.

Moved out of the removed FRIDA adapter: the intent labels describe the
dataset contract, not any particular model.
"""

ASSISTANT_INTENTS: tuple[str, ...] = (
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "appointment_status",
    "doctor_schedule",
    "doctor_information",
    "clinic_information",
    "operator",
    "unclear",
)
