"""Conversational AI endpoints (local LLM + backend tools)."""

from fastapi import APIRouter, Request

from app.ai import service as chat_service
from app.ai.schemas import ChatRequest, ChatResponse
from app.api.deps import SessionDep

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat with the clinic AI concierge",
    description=(
        "Send one user turn; pass back conversation_id to continue the "
        "dialogue. The model answers using live backend tools (clinics, "
        "doctors, slots, booking) instead of inventing facts."
    ),
)
async def chat(body: ChatRequest, session: SessionDep, http_request: Request) -> ChatResponse:
    """Run a chat turn through the LLM tool loop."""
    return await chat_service.chat(
        message=body.message,
        conversation_id=body.conversation_id,
        session=session,
        is_disconnected=http_request.is_disconnected,
    )
