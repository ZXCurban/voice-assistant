"""Map domain exceptions to HTTP statuses (simple MVP error model)."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ai.client import LlmError, LlmTimeoutError
from app.assistant.state_store import ConversationBusyError, StateStoreUnavailableError
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
    if isinstance(exc, LlmTimeoutError):
        # Local CPU inference can take minutes per turn: say so in Russian
        # and nudge toward shorter messages instead of leaking internals.
        return JSONResponse(
            status_code=504,
            content={
                "detail": (
                    "Модель отвечает слишком долго (локальный сервер перегружен). "
                    "Попробуйте отправить более короткое сообщение."
                )
            },
        )
    return JSONResponse(status_code=502, content={"detail": exc.message})


async def _conversation_busy(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConversationBusyError)
    return JSONResponse(status_code=409, content={"detail": "Повторите сообщение через секунду."})


async def _state_store_unavailable(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StateStoreUnavailableError)
    return JSONResponse(status_code=503, content={"detail": "Сервис диалога временно недоступен."})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach domain → HTTP mappings. Routers stay free of try/except."""
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(ConflictError, _conflict)
    app.add_exception_handler(ValueError, _unprocessable)
    app.add_exception_handler(LlmError, _llm_error)
    app.add_exception_handler(ConversationBusyError, _conversation_busy)
    app.add_exception_handler(StateStoreUnavailableError, _state_store_unavailable)
