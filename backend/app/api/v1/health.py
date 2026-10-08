"""Health-check endpoint (infrastructure only, no business logic)."""

from fastapi import APIRouter, HTTPException
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import RedisDep, SessionDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health_check() -> HealthResponse:
    """Return service liveness status."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, summary="Readiness check")
async def readiness_check(session: SessionDep, redis: RedisDep) -> HealthResponse:
    """Return ready only when both required backing services respond."""
    try:
        await session.execute(text("SELECT 1"))
        await redis.ping()
    except (RedisError, SQLAlchemyError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Service dependencies unavailable") from exc
    return HealthResponse(status="ready")
