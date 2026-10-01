"""Assistant orchestration boundary for the LLM/dialogue teammate.

The LLM produces AssistantRequest; AssistantOrchestrator resolves entities
through existing services and answers with AssistantResult. No SQL, no
business rules, no LLM/STT/TTS code in this package.
"""

from app.assistant.orchestrator import AssistantOrchestrator
from app.assistant.schemas import (
    AssistantContext,
    AssistantIntent,
    AssistantRequest,
    AssistantResult,
    AssistantStatus,
)

__all__ = [
    "AssistantContext",
    "AssistantIntent",
    "AssistantOrchestrator",
    "AssistantRequest",
    "AssistantResult",
    "AssistantStatus",
]
