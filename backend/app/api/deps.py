"""Shared API dependencies (placeholders, no business logic)."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.db.session import get_db_session

__all__ = ["get_db_session", "get_redis_client", "get_settings", "DBSession"]


DBSession = AsyncGenerator[AsyncSession]
