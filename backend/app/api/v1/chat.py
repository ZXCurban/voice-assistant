"""Conversational AI endpoints (local LLM, no DB access)."""

from fastapi import APIRouter

from app.ai import service as chat_service
from app.ai.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat with the clinic AI concierge",
    description=(
        "Send one user turn; pass back conversation_id to continue the "
        "dialogue. Conversational layer only: the model has no live access "
        "to schedules or bookings yet (tool wiring is the next stage)."
    ),
)
async def chat(request: ChatRequest) -> ChatResponse:
    """Proxy a chat turn to the local LLM service."""
    return await chat_service.chat(message=request.message, conversation_id=request.conversation_id)
