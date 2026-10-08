"""Reads the "face verified" flags the inference service writes to Redis on every successful recognition.

The face identity stored in Qdrant (`emp_id`) must equal the user's id as a string. Key prefix must
match RedisService.mark_face_verified in inference/services/redis_service.py.
"""

from functools import lru_cache

import redis

from api.core.config import get_settings

_PREFIX = "face_verified:"


@lru_cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url.get_secret_value(), decode_responses=True, socket_timeout=2, socket_connect_timeout=2
    )


def _parse(value: str | None) -> float | None:
    return float(value) if value is not None else None


def peek(user_id: int) -> float | None:
    """Recognition confidence if the user's face was verified recently, else None. Doesn't consume."""
    return _parse(_client().get(f"{_PREFIX}{user_id}"))


def consume(user_id: int) -> float | None:
    """Atomically reads and deletes the flag, so one recognition authorizes exactly one action."""
    return _parse(_client().getdel(f"{_PREFIX}{user_id}"))
