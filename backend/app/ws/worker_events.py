"""
Thin wrapper for Celery workers to publish task events to Redis.
Workers must NOT import app.ws.broadcaster directly (it references
the in-process WebSocket manager which doesn't exist in the worker).
"""
from app.ws.redis_relay import publish_task_event


async def emit_status(task_id: str, status: str, extra: dict | None = None) -> None:
    await publish_task_event("status_update", task_id, {"status": status, **(extra or {})})


async def emit_captcha_required(task_id: str, captcha_ctx: dict) -> None:
    await publish_task_event("captcha_required", task_id, captcha_ctx)


async def emit_completed(task_id: str, total_items: int) -> None:
    await publish_task_event("completed", task_id, {"total_items": total_items})


async def emit_failed(task_id: str, error: str) -> None:
    await publish_task_event("failed", task_id, {"error": error})
