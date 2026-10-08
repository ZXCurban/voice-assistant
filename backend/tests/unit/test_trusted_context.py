"""Tests for the signed production channel-context boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.trusted_context import TrustedChannelContextMiddleware
from app.core.config import Settings
from app.core.identity import decode_signed_context

_SECRET = "a-test-only-context-secret-that-is-long-enough"


def _signed_context(**overrides: object) -> tuple[str, str]:
    claims: dict[str, object] = {
        "subject": "patient-123",
        "role": "patient",
        "clinic_id": 7,
        "clinic_city": "Москва",
        "patient_id": 42,
        "identity_verified": True,
        "expires_at": int(time.time()) + 60,
    }
    claims.update(overrides)
    payload = json.dumps(claims, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded, signature


def test_signed_context_rejects_expired_or_tampered_claims() -> None:
    encoded, signature = _signed_context()
    context = decode_signed_context(encoded, signature, _SECRET)
    assert context.patient_id == 42
    with pytest.raises(ValueError, match="signature"):
        decode_signed_context(encoded, "0" * 64, _SECRET)

    expired, expired_signature = _signed_context(expires_at=int(time.time()) - 1)
    with pytest.raises(ValueError, match="expired"):
        decode_signed_context(expired, expired_signature, _SECRET)


@pytest.mark.asyncio
async def test_production_context_enforces_role_and_tenant_scope() -> None:
    app = FastAPI()
    app.add_middleware(
        TrustedChannelContextMiddleware,
        settings=Settings(app_env="production", channel_context_secret=_SECRET),
    )

    @app.get("/api/v1/clinics/{clinic_id}/doctors")
    async def doctors(clinic_id: int, request: Request) -> dict[str, object]:
        return {
            "clinic_id": clinic_id,
            "subject": request.state.trusted_context.subject,
        }

    @app.get("/api/v1/patients")
    async def raw_patients() -> dict[str, bool]:
        return {"ok": True}

    encoded, signature = _signed_context()
    headers = {
        "X-Trusted-Channel-Context": encoded,
        "X-Trusted-Channel-Signature": signature,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authorized = await client.get("/api/v1/clinics/7/doctors", headers=headers)
        mismatch = await client.get("/api/v1/clinics/8/doctors", headers=headers)
        forbidden = await client.get("/api/v1/patients?clinic_id=7", headers=headers)
        unauthenticated = await client.get("/api/v1/clinics/7/doctors")

    assert authorized.status_code == 200
    assert authorized.json() == {"clinic_id": 7, "subject": "patient-123"}
    assert mismatch.status_code == 404
    assert forbidden.status_code == 403
    assert unauthenticated.status_code == 401
