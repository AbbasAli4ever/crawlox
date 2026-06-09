import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger("crawlox.ws")


class ConnectionManager:
    """
    Manages active WebSocket connections grouped by channel name.
    Channel naming: task_{task_id}
    Thread-safe via asyncio lock.
    """

    def __init__(self):
        # channel_name -> set of WebSocket connections
        self._channels: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()

    async def disconnect(self, websocket: WebSocket, channel: str | None = None) -> None:
        async with self._lock:
            if channel and channel in self._channels:
                self._channels[channel].discard(websocket)
                if not self._channels[channel]:
                    del self._channels[channel]

    async def subscribe(self, websocket: WebSocket, channel: str) -> None:
        async with self._lock:
            self._channels[channel].add(websocket)
        logger.debug("WS client subscribed to channel '%s'", channel)

    async def unsubscribe(self, websocket: WebSocket, channel: str) -> None:
        async with self._lock:
            if channel in self._channels:
                self._channels[channel].discard(websocket)
                if not self._channels[channel]:
                    del self._channels[channel]

    async def broadcast(self, channel: str, event: str, data: dict) -> None:
        """Send event to all subscribers of a channel."""
        payload = json.dumps({"event": event, "data": data})
        async with self._lock:
            subscribers = set(self._channels.get(channel, set()))

        dead: list[WebSocket] = []
        for ws in subscribers:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        # Clean up dead connections
        if dead:
            async with self._lock:
                for ws in dead:
                    self._channels[channel].discard(ws)

    async def send_to(self, websocket: WebSocket, event: str, data: dict) -> None:
        """Send event to a single WebSocket connection."""
        try:
            await websocket.send_text(json.dumps({"event": event, "data": data}))
        except Exception as e:
            logger.warning("Failed to send WS message: %s", e)

    def channel_for_task(self, task_id: str) -> str:
        return f"task_{task_id}"

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, set()))


# Module-level singleton
manager = ConnectionManager()
