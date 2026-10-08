"""NLU-driven chat turns: the deterministic alternative to the LLM tool loop.

`NluChatService` owns one DialogueState per conversation (in process
memory, like the LLM history) and runs a turn through the DialogueManager.
It knows nothing about HTTP or SQLAlchemy: the caller passes the tool
executor bound to its DB session.
"""

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from app.dialogue.manager import DialogueManager, ToolExecutor
from app.dialogue.state import DialogueState, Stage
from app.nlu.engine import NluEngine, NluUnavailableError, TorchNluEngine

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

# Dialogue states kept in memory; the least recently used ones are dropped.
MAX_CONVERSATIONS = 1000


class NluChatService:
    """Runs chat turns through NLU + dialogue manager."""

    def __init__(self, engine: NluEngine, *, llm_fallback: bool = False) -> None:
        self._engine = engine
        self._llm_fallback = llm_fallback
        self._states: OrderedDict[str, DialogueState] = OrderedDict()

    @property
    def model_name(self) -> str:
        return f"nlu:{self._engine.version}"

    def _state(self, conversation_id: str) -> DialogueState:
        state = self._states.get(conversation_id)
        if state is None:
            state = DialogueState()
            self._states[conversation_id] = state
            while len(self._states) > MAX_CONVERSATIONS:
                self._states.popitem(last=False)
        else:
            self._states.move_to_end(conversation_id)
        return state

    def in_dialogue(self, conversation_id: str) -> bool:
        """True while the assistant waits for an answer to its own question."""
        state = self._states.get(conversation_id)
        return state is not None and state.stage is not Stage.IDLE

    def note_reply(self, conversation_id: str, reply: str) -> None:
        """Remember a reply produced elsewhere (fastpath): the next turn's NLU context."""
        self._state(conversation_id).last_response = reply

    async def turn(self, conversation_id: str, message: str, execute: ToolExecutor) -> str | None:
        """Reply to one message, or None if the turn should go to the LLM."""
        state = self._state(conversation_id)
        manager = DialogueManager(self._engine, execute)
        return await manager.handle(state, message, allow_defer=self._llm_fallback)

    def reset(self) -> None:
        self._states.clear()


class _Holder:
    """Process-wide service instance (models are loaded once)."""

    service: NluChatService | None = None
    load_failed: bool = False


_holder = _Holder()


def _load(settings: "Settings") -> NluChatService | None:
    if _holder.service is not None:
        return _holder.service
    if _holder.load_failed:
        return None
    try:
        engine = TorchNluEngine(
            Path(settings.nlu_model_dir), min_confidence=settings.nlu_min_confidence
        )
    except NluUnavailableError:
        logger.exception("NLU is enabled but cannot be loaded; falling back to the LLM path")
        _holder.load_failed = True
        return None
    _holder.service = NluChatService(engine, llm_fallback=settings.nlu_llm_fallback)
    return _holder.service


async def get_nlu_chat(settings: "Settings") -> NluChatService | None:
    """The NLU service if enabled and loadable, else None (LLM path)."""
    if not settings.nlu_enabled:
        return None
    if _holder.service is not None or _holder.load_failed:
        return _holder.service
    # Loading ~1 GB of weights blocks for seconds: keep the event loop free.
    return await asyncio.to_thread(_load, settings)


def install_nlu_chat(service: NluChatService | None) -> None:
    """Replace the process-wide service (tests, custom engines)."""
    _holder.service = service
    _holder.load_failed = False
