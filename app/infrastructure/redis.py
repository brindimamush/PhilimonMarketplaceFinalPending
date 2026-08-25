# app/infrastructure/redis.py
# LAYER: Infrastructure / State Management
# PURPOSE: Manages user conversation flows (FSM) using Redis.
# WHY HERE: Keeps ephemeral conversation state out of the PostgreSQL database. 
# Redis is much faster for temporary state like "waiting for user to upload a photo".

from datetime import datetime, timedelta, timezone

import orjson
from redis.asyncio import Redis


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class RedisConversationState:
    """Manages user conversation flows (e.g., registration steps) using Redis."""
    def __init__(self, redis_url: str):
        self.redis = Redis.from_url(redis_url, decode_responses=False)
        self.prefix = "conversation_state:"

    def _key(self, telegram_id: int) -> str:
        return f"{self.prefix}{telegram_id}"

    async def get(self, telegram_id: int) -> dict | None:
        data = await self.redis.get(self._key(telegram_id))
        return orjson.loads(data) if data else None

    async def set(self, telegram_id: int, *, flow: str, step: str, payload: dict | None = None) -> dict:
        """Sets the initial state for a user's conversation flow."""
        data = {"flow": flow, "step": step, "payload": payload or {}, "version": 1,
                "expires_at": (utcnow() + timedelta(days=7)).isoformat()}
        await self.redis.set(self._key(telegram_id), orjson.dumps(data), ex=7 * 24 * 3600)
        return data

    async def update(self, telegram_id: int, *, flow: str | None = None, step: str | None = None, 
                     payload_updates: dict | None = None) -> dict:
        """Updates the current state, merging new payload data with existing data."""
        current = await self.get(telegram_id) or {}
        payload = {**(current.get("payload") or {}), **(payload_updates or {})}
        data = {"flow": flow or current.get("flow"), "step": step or current.get("step"), 
                "payload": payload, "version": int(current.get("version", 0)) + 1,
                "expires_at": (utcnow() + timedelta(days=7)).isoformat()}
        await self.redis.set(self._key(telegram_id), orjson.dumps(data), ex=7 * 24 * 3600)
        return data

    async def clear(self, telegram_id: int) -> None:
        """Clears the conversation state when a flow is completed or cancelled."""
        await self.redis.delete(self._key(telegram_id))