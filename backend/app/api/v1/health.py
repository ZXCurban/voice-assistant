"""Health-check endpoint (infrastructure only, no business logic)."""

from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Health check")
def health_check() -> HealthResponse:
    """Return service liveness status."""
    return HealthResponse(status="ok")
