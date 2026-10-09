"""Evaluation schemas: dataset item + prediction record."""

from pydantic import BaseModel, Field


class EvalItem(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    expected_intent: str
    expected_needs_human: bool
    expected_needs_clarification: bool
    expected_workflow: str
    expected_tool: str | None = None
    category: str
    notes: str = ""
    # Optional prior-turn context for follow-up items (v2). When present,
    # harnesses must supply it (e.g. assembled dialogue history).
    context: str | None = None


class EvalPrediction(BaseModel):
    id: str
    predicted_intent: str
    predicted_needs_human: bool = False
    predicted_needs_clarification: bool = False
    predicted_workflow: str = ""
    predicted_tool: str | None = None
    confidence: float = 0.0
    latency_ms: float = 0.0
    backend: str = "unknown"
    error: str | None = None
