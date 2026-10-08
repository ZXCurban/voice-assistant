"""Result of one NLU pass."""

from pydantic import BaseModel, ConfigDict, Field


class NluParse(BaseModel):
    """Intent + validated slots for one user utterance."""

    model_config = ConfigDict(frozen=True)

    intent: str
    # Calibrated probability of the predicted intent (0..1).
    confidence: float = Field(ge=0.0, le=1.0)
    # True when `confidence` reached the calibrated threshold.
    confident: bool
    slots: dict[str, str] = Field(default_factory=dict)
