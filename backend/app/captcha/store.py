import json
import logging

import redis.asyncio as aioredis

from app.captcha.detector import CaptchaContext
from app.config import settings

logger = logging.getLogger("crawlox.captcha")

_CAPTCHA_TTL = 600  # 10 minutes — user has this long to solve


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def save_captcha_context(task_id: str, ctx: CaptchaContext) -> None:
    """Persist CAPTCHA context to Redis so the WS server can forward it to the client."""
    r = _redis()
    payload = {
        "task_id": task_id,
        "captcha_type": ctx.captcha_type,
        "sitekey": ctx.sitekey,
        "page_url": ctx.page_url,
        "screenshot_b64": ctx.screenshot_b64,
    }
    await r.set(f"captcha:context:{task_id}", json.dumps(payload), ex=_CAPTCHA_TTL)
    await r.aclose()
    logger.info("CAPTCHA context saved for task %s (type=%s)", task_id, ctx.captcha_type)


async def get_captcha_context(task_id: str) -> dict | None:
    """Retrieve saved CAPTCHA context."""
    r = _redis()
    raw = await r.get(f"captcha:context:{task_id}")
    await r.aclose()
    return json.loads(raw) if raw else None


async def publish_solution(task_id: str, solution: str) -> None:
    """Publish a CAPTCHA solution from the WebSocket handler to the waiting worker."""
    r = _redis()
    await r.set(f"captcha:solution:{task_id}", solution, ex=_CAPTCHA_TTL)
    await r.publish(f"captcha:solved:{task_id}", solution)
    await r.aclose()
    logger.info("CAPTCHA solution published for task %s", task_id)


async def wait_for_solution(task_id: str, timeout_s: int = 300) -> str | None:
    """
    Block until a CAPTCHA solution arrives via Redis pubsub.
    Returns the solution token or None on timeout.
    Called from the Celery worker — runs inside asyncio.
    """
    import asyncio

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(f"captcha:solved:{task_id}")

    solution = None
    try:
        # Fast path: solution may already be stored before we subscribed
        # (publish_solution writes the key AND publishes, so this avoids the race
        # where the human solves faster than the worker subscribes).
        existing = await r.get(f"captcha:solution:{task_id}")
        if existing:
            return existing

        # Poll with a bounded timeout so the worker never hangs past timeout_s.
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=5.0
            )
            if message and message.get("type") == "message":
                solution = message["data"]
                break
            # Re-check the key each poll in case publish raced the subscribe
            existing = await r.get(f"captcha:solution:{task_id}")
            if existing:
                solution = existing
                break
    finally:
        await pubsub.unsubscribe(f"captcha:solved:{task_id}")
        await pubsub.aclose()
        await r.aclose()

    return solution


async def clear_captcha(task_id: str) -> None:
    """Clean up Redis keys after CAPTCHA is solved."""
    r = _redis()
    await r.delete(
        f"captcha:context:{task_id}",
        f"captcha:solution:{task_id}",
    )
    await r.aclose()
