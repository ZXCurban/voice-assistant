"""Async Redis client holder (lazy, no business logic).

Decision: lazy module-level singleton, same rationale as db.session.
No I/O happens until the first Redis command.
"""

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis_client() -> Redis:
    """Return a process-wide Redis client (no I/O until first command)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis_client() -> None:
    """Close the shared Redis client (call on application shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
