"""
WebSocket plumbing for real-time, continuous face streaming.

Key real-time trick: each connection has a frame "mailbox" of size 1. The
network-facing coroutine just overwrites it with the newest frame; a
dedicated worker thread continuously pulls from it and runs the heavy
detection/recognition pipeline. If processing can't keep up with the camera's
frame rate, older frames are simply dropped instead of queueing up, so the
client always sees results for the most recent reality, not a growing lag.
"""

import asyncio
import logging
import queue
import threading
import time
from dataclasses import asdict
from typing import Optional

import numpy as np

from inference import config
from inference.pipeline import get_pipeline
from inference.services.redis_service import get_redis

logger = logging.getLogger("inference.ws_manager")


class FrameMailbox:
    """A size-1 'queue' that always holds only the latest frame."""

    def __init__(self):
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._event = threading.Event()

    def put(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
        self._event.set()

    def get(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        if not self._event.wait(timeout=timeout):
            return None
        with self._lock:
            frame, self._frame = self._frame, None
            self._event.clear()
            return frame


class StreamSession:
    """Owns the background worker thread for a single WebSocket connection."""

    def __init__(self, connection_id: str, loop: asyncio.AbstractEventLoop):
        self.connection_id = connection_id
        self.loop = loop
        self.mailbox = FrameMailbox()
        self.result_queue: "asyncio.Queue" = asyncio.Queue(maxsize=8)
        self.mode = "recognize_only"  # or "attendance"
        self.action = "check_in"  # check_in | check_out, only used in attendance mode
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.pipeline = get_pipeline()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.mailbox.put(None)  # wake the worker so it can exit promptly

    def submit_frame(self, frame: np.ndarray) -> None:
        self.mailbox.put(frame)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            frame = self.mailbox.get(timeout=1.0)
            if frame is None:
                continue

            started = time.time()
            try:
                action = self.action if self.mode == "attendance" else None
                result = self.pipeline.process_frame(frame, connection_id=self.connection_id, mark_attendance_action=action)
                payload = {
                    "type": "result",
                    "faces": [asdict(f) for f in result.faces],
                    "processing_ms": round((time.time() - started) * 1000, 1),
                }
            except Exception as exc:
                logger.exception("Error processing frame for %s: %s", self.connection_id, exc)
                payload = {"type": "error", "message": str(exc)}

            self._push_result(payload)

    def _push_result(self, payload: dict) -> None:
        async def _put():
            if self.result_queue.full():
                try:
                    self.result_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await self.result_queue.put(payload)

        asyncio.run_coroutine_threadsafe(_put(), self.loop)


class DashboardBroadcaster:
    """Bridges Redis pub/sub events to every connected dashboard WebSocket."""

    def __init__(self):
        self._clients: set = set()
        self._lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None

    async def register(self, websocket) -> None:
        async with self._lock:
            self._clients.add(websocket)
            if self._listener_task is None:
                self._listener_task = asyncio.create_task(self._listen())

    async def unregister(self, websocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def _listen(self) -> None:
        redis_service = get_redis()
        async for event in redis_service.subscribe_events():
            dead = []
            async with self._lock:
                clients = list(self._clients)
            for client in clients:
                try:
                    await client.send_json(event)
                except Exception:
                    dead.append(client)
            if dead:
                async with self._lock:
                    for client in dead:
                        self._clients.discard(client)


dashboard_broadcaster = DashboardBroadcaster()
