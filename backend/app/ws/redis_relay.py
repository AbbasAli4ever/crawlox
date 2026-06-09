"""
Redis relay — bridges Celery worker status updates to WebSocket clients.

Workers can't directly call the WebSocket broadcaster (different process).
Instead they publish to Redis, and a background task in the FastAPI process
subscribes and relays events to connected WebSocket clients.
"""
import asyncio
import json
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.ws.broadcaster import (
    broadcast_captcha_required,
    broadcast_completed,
    broadcast_failed,
    broadcast_status,
)

logger = logging.getLogger("crawlox.ws")

CHANNEL = "crawlox:task_events"
_relay_task: asyncio.Task | None = None


async def publish_task_event(event: str, task_id: str, data: dict) -> None:
    """Publish a task event to Redis. Works from any async context."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        payload = json.dumps({"event": event, "task_id": task_id, "data": data})
        await r.publish(CHANNEL, payload)
    finally:
        await r.aclose()


def publish_task_event_sync(event: str, task_id: str, data: dict) -> None:
    """Sync version for Celery workers (different process, own event loop)."""
    import asyncio
    asyncio.run(publish_task_event(event, task_id, data))


async def _relay_loop() -> None:
    """Subscribe to Redis and relay task events to WebSocket clients."""
    logger.info("WS relay loop started — listening on %s", CHANNEL)
    while True:
        r = None
        pubsub = None
        try:
            # socket_timeout=None keeps the connection open indefinitely
            r = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=None,
                socket_keepalive=True,
            )
            pubsub = r.pubsub()
            await pubsub.subscribe(CHANNEL)
            logger.debug("Relay subscribed to Redis channel %s", CHANNEL)

            while True:
                # get_message with timeout avoids blocking forever if Redis is silent
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=30.0
                )
                if message is None:
                    # No message — ping to keep connection alive
                    await r.ping()
                    continue

                if message["type"] != "message":
                    continue

                try:
                    payload = json.loads(message["data"])
                    event = payload["event"]
                    task_id = payload["task_id"]
                    data = payload.get("data", {})

                    if event == "status_update":
                        await broadcast_status(task_id, data.get("status", ""), data)
                    elif event == "captcha_required":
                        await broadcast_captcha_required(task_id, data)
                    elif event == "completed":
                        await broadcast_completed(task_id, data.get("total_items", 0))
                    elif event == "failed":
                        await broadcast_failed(task_id, data.get("error", ""))

                except Exception as e:
                    logger.warning("Relay error processing message: %s", e)

        except Exception as e:
            logger.error("WS relay loop error: %s — reconnecting in 3s", e)
            await asyncio.sleep(3)
        finally:
            try:
                if pubsub:
                    await pubsub.aclose()
                if r:
                    await r.aclose()
            except Exception:
                pass


def start_relay(app) -> None:
    """Start the relay loop as a background asyncio task on app startup."""
    @app.on_event("startup")
    async def _start():
        global _relay_task
        _relay_task = asyncio.create_task(_relay_loop())
        logger.info("WS Redis relay started")

    @app.on_event("shutdown")
    async def _stop():
        global _relay_task
        if _relay_task:
            _relay_task.cancel()
            try:
                await _relay_task
            except asyncio.CancelledError:
                pass
