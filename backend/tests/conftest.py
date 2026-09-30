"""Shared pytest fixtures (infrastructure only)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """Return a synchronous test client bound to a fresh app instance."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
