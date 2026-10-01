"""Assistant contract validation (no DB): request/result shapes."""

import pytest
from pydantic import ValidationError

from app.assistant.schemas import AssistantRequest, AssistantResult


def test_valid_request_defaults() -> None:
    req = AssistantRequest(intent="find_slots", clinic_id=1)
    assert req.confirmed is False
    assert req.context is None
    assert req.date is None


def test_invalid_intent_rejected() -> None:
    with pytest.raises(ValidationError):
        AssistantRequest(intent="teleport_patient", clinic_id=1)  # type: ignore[arg-type]


def test_result_defaults() -> None:
    res = AssistantResult(status="success", code="OK", message="done")
    assert res.requires_confirmation is False
    assert res.details == {}


def test_confirmation_result_shape() -> None:
    res = AssistantResult(
        status="confirmation_required",
        code="CONFIRM_BOOKING",
        message="Confirm?",
        requires_confirmation=True,
        details={"slot": {"starts_at": "2026-10-06T08:00:00Z"}},
    )
    assert res.requires_confirmation is True
    assert res.details["slot"]["starts_at"] == "2026-10-06T08:00:00Z"
