"""Text assistant endpoint (NLU + backend services + deterministic response)."""

from fastapi import APIRouter, Request

from app.ai import service as chat_service
from app.ai.schemas import ChatRequest, ChatResponse
from app.api.deps import AssistantIdentityDep, RedisDep, SessionDep

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat with the clinic AI concierge",
    description=(
        "Send one user turn; pass back conversation_id to continue the "
        "dialogue. Requests are parsed and resolved against clinic backend "
        "services."
    ),
)
async def chat(
    body: ChatRequest,
    session: SessionDep,
    redis: RedisDep,
    identity: AssistantIdentityDep,
    http_request: Request,
) -> ChatResponse:
    """Run one turn through the assistant pipeline."""
    return await chat_service.chat(
        message=body.message,
        conversation_id=body.conversation_id,
        session=session,
        redis=redis,
        identity=identity,
        is_disconnected=http_request.is_disconnected,
    )
