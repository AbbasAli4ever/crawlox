import time
import uuid

import redis.asyncio as aioredis

from app.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def is_rate_limited(key: str, max_attempts: int, window_seconds: int) -> bool:
    """Sliding window counter. Returns True if the caller is over the limit."""
    r = get_redis()
    now = time.time()
    window_start = now - window_seconds

    # Use a unique member so same-second requests don't overwrite each other
    member = f"{now}:{uuid.uuid4()}"

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    results = await pipe.execute()

    count = results[2]
    return count > max_attempts  # count includes current request; > 5 means 6th+ attempt
