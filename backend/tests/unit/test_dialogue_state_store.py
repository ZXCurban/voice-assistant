"""Redis dialogue-state persistence and scope isolation."""

from __future__ import annotations

from typing import Any, cast

import pytest
from redis.asyncio import Redis

from app.assistant.state_store import RedisDialogueStateStore


class _MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> str | None:
        _ = ex
        if nx and key in self.values:
            return None
        self.values[key] = value
        return "OK"

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        _ = script
        assert numkeys == 1
        key, token = keys_and_args
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_state_persists_but_is_isolated_by_subject_and_clinic() -> None:
    redis = _MemoryRedis()
    store = RedisDialogueStateStore(cast(Redis, redis))

    async with store.conversation("same-id", subject="patient-1", clinic_id=7) as state:
        state.intent = "book_appointment"
        state.phase = "WAITING_CLARIFICATION"
        state.clinic_city = "Warszawa"

    async with store.conversation("same-id", subject="patient-1", clinic_id=7) as resumed:
        assert resumed.intent == "book_appointment"
        assert resumed.phase == "WAITING_CLARIFICATION"
        assert resumed.clinic_city == "Warszawa"

    async with store.conversation("same-id", subject="patient-2", clinic_id=7) as other_user:
        assert other_user.intent is None

    async with store.conversation("same-id", subject="patient-1", clinic_id=8) as other_clinic:
        assert other_clinic.intent is None
