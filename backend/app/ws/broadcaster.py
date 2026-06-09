import logging

from app.ws.events import (
    CAPTCHA_REQUIRED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_STATUS_UPDATE,
)
from app.ws.manager import manager

logger = logging.getLogger("crawlox.ws")


async def broadcast_status(task_id: str, status: str, extra: dict | None = None) -> None:
    """Push a task status update to all subscribers of task_{task_id}."""
    channel = manager.channel_for_task(task_id)
    data = {"task_id": task_id, "status": status, **(extra or {})}
    await manager.broadcast(channel, TASK_STATUS_UPDATE, data)
    logger.debug("Broadcast status '%s' for task %s (%d subscribers)", status, task_id, manager.subscriber_count(channel))


async def broadcast_captcha_required(task_id: str, captcha_ctx: dict) -> None:
    """Push captcha:required event with full context to subscribers."""
    channel = manager.channel_for_task(task_id)
    await manager.broadcast(channel, CAPTCHA_REQUIRED, captcha_ctx)
    logger.info("Broadcast captcha:required for task %s", task_id)


async def broadcast_completed(task_id: str, total_items: int) -> None:
    channel = manager.channel_for_task(task_id)
    await manager.broadcast(channel, TASK_COMPLETED, {
        "task_id": task_id,
        "total_items": total_items,
    })


async def broadcast_failed(task_id: str, error: str) -> None:
    channel = manager.channel_for_task(task_id)
    await manager.broadcast(channel, TASK_FAILED, {
        "task_id": task_id,
        "error": error,
    })
