"""Chat API contracts (HTTP layer only, no business logic)."""

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Incoming chat turn. conversation_id is optional on the first turn."""

    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChatResponse(BaseModel):
    """Single assistant reply with the conversation to continue."""

    conversation_id: str
    message: str
    model: str
