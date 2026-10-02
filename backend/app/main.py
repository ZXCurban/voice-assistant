"""FastAPI application entrypoint (no business logic)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.redis import close_redis_client, get_redis_client
from app.db.session import dispose_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create shared clients on startup, release them on shutdown.

    Engine/Redis creation performs no I/O, so startup never fails
    when infrastructure is unavailable (important for tests/CI).
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    get_engine()
    get_redis_client()
    yield
    await dispose_engine()
    await close_redis_client()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Public contract: GET /health stays at the root.
    Settings.api_v1_prefix is reserved for future domain routers only.
    """
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"))
    return app


app = create_app()
