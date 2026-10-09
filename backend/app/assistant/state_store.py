"""Short-lived, tenant- and identity-scoped dialogue state in Redis."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.assistant.dialogue import STATE_TTL, DialogueState

_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class ConversationBusyError(RuntimeError):
    """Raised when another request is already processing this conversation."""


class StateStoreUnavailableError(RuntimeError):
    """Raised when durable conversation state cannot be read or written."""


class RedisDialogueStateStore:
    """Load/save one conversation at a time, isolated by principal and clinic."""

    def __init__(self, redis: Redis, ttl_seconds: int = int(STATE_TTL.total_seconds())) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def _key(self, conversation_id: str, subject: str, clinic_id: int | None) -> str:
        scope = sha256(f"{subject}:{clinic_id or 'unbound'}".encode()).hexdigest()[:24]
        conversation = sha256(conversation_id.encode()).hexdigest()
        return f"assistant:dialogue:{scope}:{conversation}"

    @asynccontextmanager
    async def conversation(
        self,
        conversation_id: str,
        *,
        subject: str,
        clinic_id: int | None,
    ) -> AsyncIterator[DialogueState]:
        key = self._key(conversation_id, subject, clinic_id)
        lock_key = f"{key}:lock"
        try:
            token = await self._acquire(lock_key)
        except RedisError as exc:
            raise StateStoreUnavailableError from exc
        try:
            raw = await self.redis.get(key)
            if raw is None:
                state = DialogueState(conversation_id=conversation_id)
            else:
                try:
                    state = DialogueState.model_validate_json(raw)
                except ValidationError:
                    state = DialogueState(conversation_id=conversation_id)
                if state.expired():
                    state.clear_workflow()
                    state.conversation_id = conversation_id
            yield state
            await self.redis.set(key, state.model_dump_json(), ex=self.ttl_seconds)
        except RedisError as exc:
            raise StateStoreUnavailableError from exc
        finally:
            try:
                await self.redis.eval(_RELEASE_LOCK, 1, lock_key, token)
            except RedisError as exc:
                raise StateStoreUnavailableError from exc

    async def _acquire(self, lock_key: str) -> str:
        # Lock TTL covers a long NLU/LLM turn (up to 5 min) so the lock
        # cannot expire mid-turn, but stays well below the state TTL to
        # bound the blackout after a process crash.
        lock_ttl = max(60, min(self.ttl_seconds, 300))
        token = uuid.uuid4().hex
        for _ in range(80):
            acquired = await self.redis.set(lock_key, token, nx=True, ex=lock_ttl)
            if acquired:
                return token
            await asyncio.sleep(0.025)
        raise ConversationBusyError("conversation is being processed; retry the turn")
