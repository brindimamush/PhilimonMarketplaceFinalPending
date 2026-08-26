# app/core/rate_limiter.py
import time

from redis.asyncio import Redis

from app.core.exceptions import LocalizedDomainError


async def check_rate_limit(redis: Redis, user_id: int, action: str, limit: int, window_seconds: int):
    """
    Enforces a sliding window rate limit using Redis sorted sets.
    WHY: Spec requires protecting the bot against accidental loops and abuse.
    """
    key = f"rate_limit:{action}:{user_id}"
    now = time.time()
    
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    results = await pipe.execute()
    
    if results[2] > limit:
        raise LocalizedDomainError("error.rate_limited")