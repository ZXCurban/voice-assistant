"""Shared pytest fixtures (infrastructure only)."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.session import get_db_session
from app.main import create_app
from app.models import Base


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Return a synchronous test client bound to a fresh app instance."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def db_session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    """Isolated file-SQLite session per test (PG semantics verified separately).

    Partial unique indexes are declared with both postgresql_where and
    sqlite_where, so double-booking protection behaves the same here.
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db?timeout=30"
    engine = create_async_engine(url, connect_args={"timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def api_client(db_session: AsyncSession) -> AsyncIterator[TestClient]:
    """TestClient whose DB dependency is the isolated SQLite session."""

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as test_client:
        yield test_client
