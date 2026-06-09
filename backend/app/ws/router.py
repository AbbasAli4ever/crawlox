import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.future import select

from app.auth.jwt import decode_token
from app.captcha.store import get_captcha_context, publish_solution
from app.db.base import AsyncSessionLocal
from app.db.models import Task, User
from app.ws.events import (
    ACTION_CAPTCHA_SOLUTION,
    ACTION_SUBSCRIBE,
    ACTION_UNSUBSCRIBE,
    CAPTCHA_REQUIRED,
    ERROR,
    SUBSCRIBED,
    TASK_STATUS_UPDATE,
)
from app.ws.manager import manager

logger = logging.getLogger("crawlox.ws")

router = APIRouter(tags=["websocket"])


async def _authenticate(websocket: WebSocket) -> User | None:
    """
    Authenticate WebSocket connection via JWT.
    Token passed as query param: /ws?token=<access_token>
    Returns User on success, None on failure (caller should close).
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return None

    try:
        user_id = decode_token(token, "access")
    except ValueError:
        await websocket.close(code=4001, reason="Invalid token")
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()

    if not user:
        await websocket.close(code=4001, reason="User not found")
        return None

    return user


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    user = await _authenticate(websocket)
    if not user:
        return

    await manager.connect(websocket)
    subscribed_channel: str | None = None
    logger.info("WS connected: user=%s", user.email)

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to(websocket, ERROR, {"message": "Invalid JSON"})
                continue

            action = msg.get("action", "")

            # --- subscribe ---
            if action == ACTION_SUBSCRIBE:
                channel = msg.get("channel", "")
                if not channel.startswith("task_"):
                    await manager.send_to(websocket, ERROR, {"message": "Channel must start with 'task_'"})
                    continue

                task_id = channel.removeprefix("task_")

                # Validate task_id is a well-formed UUID before querying
                try:
                    task_uuid = uuid.UUID(task_id)
                except (ValueError, AttributeError, TypeError):
                    await manager.send_to(websocket, ERROR, {"message": "Invalid task_id in channel"})
                    continue

                # Verify user owns this task
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Task).where(
                            Task.id == task_uuid,
                            Task.user_id == user.id,
                        )
                    )
                    task = result.scalar_one_or_none()

                if not task:
                    await manager.send_to(websocket, ERROR, {"message": "Task not found or access denied"})
                    continue

                # Unsubscribe from previous channel if any
                if subscribed_channel:
                    await manager.unsubscribe(websocket, subscribed_channel)

                subscribed_channel = channel
                await manager.subscribe(websocket, channel)
                await manager.send_to(websocket, SUBSCRIBED, {"channel": channel})

                # Send current task status immediately on subscribe
                await manager.send_to(websocket, TASK_STATUS_UPDATE, {
                    "task_id": task_id,
                    "status": task.status,
                })

                # If task is already waiting for CAPTCHA, send the payload immediately
                if task.status == "captcha_needed":
                    captcha_ctx = await get_captcha_context(task_id)
                    if captcha_ctx:
                        await manager.send_to(websocket, CAPTCHA_REQUIRED, captcha_ctx)

            # --- unsubscribe ---
            elif action == ACTION_UNSUBSCRIBE:
                channel = msg.get("channel", "")
                if subscribed_channel == channel:
                    await manager.unsubscribe(websocket, channel)
                    subscribed_channel = None

            # --- captcha solution ---
            elif action == ACTION_CAPTCHA_SOLUTION:
                task_id = msg.get("task_id", "")
                solution = (msg.get("solution") or "").strip()

                if not task_id or not solution:
                    await manager.send_to(websocket, ERROR, {"message": "Missing task_id or solution"})
                    continue

                # Validate task_id is a well-formed UUID before querying
                try:
                    task_uuid = uuid.UUID(task_id)
                except (ValueError, AttributeError, TypeError):
                    await manager.send_to(websocket, ERROR, {"message": "Invalid task_id format"})
                    continue

                # Verify ownership
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Task).where(
                            Task.id == task_uuid,
                            Task.user_id == user.id,
                        )
                    )
                    task = result.scalar_one_or_none()

                if not task or task.status != "captcha_needed":
                    await manager.send_to(websocket, ERROR, {"message": "Task not in captcha_needed state"})
                    continue

                await publish_solution(task_id, solution)
                logger.info("CAPTCHA solution received for task %s from user %s", task_id, user.email)

            else:
                await manager.send_to(websocket, ERROR, {"message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        logger.info("WS disconnected: user=%s channel=%s", user.email, subscribed_channel)
    finally:
        if subscribed_channel:
            await manager.unsubscribe(websocket, subscribed_channel)
