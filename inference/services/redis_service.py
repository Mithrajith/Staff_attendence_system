"""
Redis-backed helpers for the real-time pipeline:

    * Pub/Sub broadcast of recognition events (so dashboards / other services
      can subscribe to live activity, independent of which worker/connection
      produced it).
    * Short-lived cache of "who was just recognized" to avoid re-announcing
      the same identity on every single frame (de-dupe).
    * Attendance debounce: prevents marking the same employee's check-in /
      check-out repeatedly while they linger in front of the camera.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis
import redis.asyncio as aioredis

from inference import config

logger = logging.getLogger("inference.redis_service")


class RedisService:
    def __init__(self, url: str = config.REDIS_URL):
        self.url = url
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.async_client = aioredis.Redis.from_url(url, decode_responses=True)

    def health_check(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception as exc:
            logger.warning("Redis health check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Recognition de-dupe cache (per connection, short TTL)
    # ------------------------------------------------------------------
    def was_recently_seen(self, connection_id: str, identity: str) -> bool:
        key = f"recent:{connection_id}:{identity}"
        return self.client.exists(key) == 1

    def mark_recently_seen(self, connection_id: str, identity: str) -> None:
        key = f"recent:{connection_id}:{identity}"
        self.client.set(key, "1", ex=config.RECOGNITION_CACHE_TTL)

    # ------------------------------------------------------------------
    # Attendance debounce (per employee + action, longer TTL)
    # ------------------------------------------------------------------
    def can_mark_attendance(self, emp_id: str, action: str) -> bool:
        key = f"attendance_debounce:{action}:{emp_id}"
        return self.client.exists(key) == 0

    def debounce_attendance(self, emp_id: str, action: str) -> None:
        key = f"attendance_debounce:{action}:{emp_id}"
        self.client.set(key, "1", ex=config.ATTENDANCE_DEBOUNCE_TTL)

    # ------------------------------------------------------------------
    # Face verification (consumed by the user API to gate check-in/check-out)
    # ------------------------------------------------------------------
    def mark_face_verified(self, identity: str, confidence: float) -> None:
        """Flags `identity` as having just been recognized. The API reads (and consumes) this key.

        The key prefix must match api/core/face_verification.py.
        """
        try:
            self.client.set(f"face_verified:{identity}", f"{confidence:.4f}", ex=config.FACE_VERIFY_TTL)
        except Exception as exc:
            logger.warning("Failed to mark face verified for %s: %s", identity, exc)

    # ------------------------------------------------------------------
    # Pub/Sub broadcast
    # ------------------------------------------------------------------
    def publish_event(self, event_type: str, data: dict) -> None:
        message = json.dumps(
            {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            default=str,
        )
        try:
            self.client.publish(config.REDIS_EVENTS_CHANNEL, message)
        except Exception as exc:
            logger.warning("Failed to publish Redis event: %s", exc)

    async def subscribe_events(self):
        """Async generator yielding decoded JSON events as they are published."""
        pubsub = self.async_client.pubsub()
        await pubsub.subscribe(config.REDIS_EVENTS_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    yield json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    continue
        finally:
            await pubsub.unsubscribe(config.REDIS_EVENTS_CHANNEL)
            await pubsub.close()


_redis_singleton: Optional[RedisService] = None


def get_redis() -> RedisService:
    global _redis_singleton
    if _redis_singleton is None:
        _redis_singleton = RedisService()
    return _redis_singleton
