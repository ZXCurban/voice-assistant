"""Health-check response schema."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: str
