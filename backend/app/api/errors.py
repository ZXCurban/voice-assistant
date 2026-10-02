"""Map domain exceptions to HTTP statuses (simple MVP error model)."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ai.client import LlmError, LlmTimeoutError
from app.core.errors import ConflictError, NotFoundError


async def _not_found(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, NotFoundError)
    return JSONResponse(status_code=404, content={"detail": exc.message})


async def _conflict(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConflictError)
    return JSONResponse(status_code=409, content={"detail": exc.message})


async def _unprocessable(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ValueError)
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def _llm_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LlmError)
    status = 504 if isinstance(exc, LlmTimeoutError) else 502
    return JSONResponse(status_code=status, content={"detail": exc.message})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach domain → HTTP mappings. Routers stay free of try/except."""
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(ConflictError, _conflict)
    app.add_exception_handler(ValueError, _unprocessable)
    app.add_exception_handler(LlmError, _llm_error)
