"""Readiness checks require a live database session and Redis response."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis_client
from app.db.session import get_db_session
from app.main import create_app


class _RedisProbe:
    def __init__(self, available: bool) -> None:
        self.available = available

    async def ping(self) -> bool:
        if not self.available:
            raise RedisConnectionError("redis unavailable")
        return True


async def _client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_readiness_reports_dependency_health(db_session: AsyncSession) -> None:
    app = create_app()

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_redis_client] = lambda: cast(Redis, _RedisProbe(True))
    async for client in _client(app):
        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}

    app.dependency_overrides[get_redis_client] = lambda: cast(Redis, _RedisProbe(False))
    async for client in _client(app):
        unavailable = await client.get("/ready")
        assert unavailable.status_code == 503
