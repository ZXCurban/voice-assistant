"""Smoke tests: app boots and infrastructure holders behave lazily."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.db.session import get_engine


def test_health_endpoint(client: TestClient) -> None:
    """GET /health returns 200 and expected payload."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_loads() -> None:
    """Settings can be constructed from defaults/example env."""
    settings = get_settings()
    assert settings.app_name
    assert settings.database_url
    assert settings.redis_url


def test_engine_and_redis_are_lazy() -> None:
    """Engine/Redis object creation performs no I/O (safe without infra)."""
    assert get_engine() is not None
    assert get_redis_client() is not None
