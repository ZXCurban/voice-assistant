"""Shared API dependencies for routers.

Re-exports infra helpers and provides SessionDep for domain endpoints.
"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, is_production
from app.core.identity import AssistantIdentity, TrustedChannelContext
from app.db.redis import get_redis_client
from app.db.session import get_db_session

__all__ = ["get_db_session", "get_redis_client", "get_settings", "SessionDep"]


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[Redis, Depends(get_redis_client)]


async def get_assistant_identity(request: Request) -> AssistantIdentity:
    """Return trusted channel claims or an isolated synthetic local identity."""
    context = getattr(request.state, "trusted_context", None)
    if isinstance(context, TrustedChannelContext):
        return AssistantIdentity(
            subject=context.subject,
            clinic_id=context.clinic_id,
            clinic_city=context.clinic_city,
            patient_id=context.patient_id,
            verified_phone=context.verified_phone,
            full_name=context.full_name,
            identity_verified=context.identity_verified,
            tenant_locked=context.role == "patient",
        )
    if is_production(get_settings()):
        raise PermissionError("trusted channel context is required")
    return AssistantIdentity(subject="local-demo")


AssistantIdentityDep = Annotated[AssistantIdentity, Depends(get_assistant_identity)]
