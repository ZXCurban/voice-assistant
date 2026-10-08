"""Identity claims supplied by an authenticated channel gateway."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class TrustedChannelContext(BaseModel):
    """Short-lived tenant and patient identity asserted by a trusted adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, max_length=160)
    role: Literal["patient", "clinic_admin", "platform_admin"]
    clinic_id: int | None = Field(default=None, gt=0)
    clinic_city: str | None = Field(default=None, min_length=2, max_length=100)
    patient_id: int | None = Field(default=None, gt=0)
    identity_verified: bool = False
    verified_phone: str | None = Field(default=None, min_length=5, max_length=50)
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    expires_at: int

    @model_validator(mode="after")
    def validate_scope(self) -> TrustedChannelContext:
        if self.role == "platform_admin":
            return self
        if self.clinic_id is None:
            raise ValueError("clinic_id is required for this role")
        if self.role == "patient":
            if not self.identity_verified:
                raise ValueError("patient identity must be verified")
            if self.clinic_city is None:
                raise ValueError("patient channel must be bound to a clinic city")
            if self.patient_id is None and not (self.verified_phone and self.full_name):
                raise ValueError("patient profile or verified first-visit data is required")
        return self


class AssistantIdentity(BaseModel):
    """Minimal identity projection consumed by the dialogue workflow."""

    subject: str
    clinic_id: int | None = None
    clinic_city: str | None = None
    patient_id: int | None = None
    verified_phone: str | None = None
    full_name: str | None = None
    identity_verified: bool = False
    tenant_locked: bool = False


def decode_signed_context(encoded: str, signature: str, secret: str) -> TrustedChannelContext:
    """Verify and parse a base64url JSON context signed by the channel gateway."""
    if len(encoded) > 4096:
        raise ValueError("trusted context is too large")
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid trusted context signature")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.b64decode(padded.encode(), altchars=b"-_", validate=True)
        context = TrustedChannelContext.model_validate(json.loads(payload))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("invalid trusted context payload") from exc
    now = int(time.time())
    if context.expires_at <= now or context.expires_at > now + 300:
        raise ValueError("trusted context is expired or has an invalid lifetime")
    return context
