"""Shared API dependencies for routers.

Re-exports infra helpers and provides SessionDep for domain endpoints.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.redis import get_redis_client
from app.db.session import get_db_session

__all__ = ["get_db_session", "get_redis_client", "get_settings", "SessionDep"]


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
