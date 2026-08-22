"""Redis client.

Three jobs in this system: Celery's broker, a cache, and the pub/sub channel
that fans WebSocket events out to whichever API replica holds a user's socket
(docs/03-backend-architecture.md §9).
"""

from typing import Any

import redis.asyncio as aioredis

from app.config import settings

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def check_redis() -> dict[str, Any]:
    """Used by /health. Never raises — reports."""
    try:
        await get_redis().ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def user_channel(user_id: str) -> str:
    """Pub/sub channel a user's WebSocket connections subscribe to."""
    return f"user:{user_id}"


def new_connection() -> aioredis.Redis:
    """A client of its own, for a caller that will hold it open.

    **A subscription is not a request.** `get_redis()` returns a shared pool
    sized for short operations — a rate-limit increment, an idempotency lookup —
    and a `SUBSCRIBE` holds its connection for the entire life of a WebSocket.
    Taking those from the shared pool means a few hundred connected browsers can
    starve every other Redis call in the process, and the first symptom is
    sign-ins timing out for reasons nothing connects to the socket.

    It also keeps the connection on the caller's own event loop, which is what
    makes a pooled client fail with "attached to a different loop" the moment
    anything runs more than one.
    """
    connection: aioredis.Redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
    )
    return connection
