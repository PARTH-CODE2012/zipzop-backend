"""Rate limiting, in Redis.

A **fixed window** counter: one key per (bucket, identity, window), incremented
and expired atomically. Its known weakness is the boundary — a caller can spend
a full allowance at the end of one window and another at the start of the next,
so the true worst case is twice the limit across two adjacent seconds.

That is accepted here on purpose. The limits exist to stop brute force and
runaway clients, not to meter a paid resource; a sliding-window log would cost
a sorted set per caller and a trim on every request to fix a factor of two that
nothing in the product depends on. Credits, which *are* metered, are counted in
Postgres with a ledger — not here.

The counter lives in Redis rather than in the process so that limits hold
across API replicas. If Redis is down, requests are **allowed**: a throttle
that fails closed turns a cache outage into a total outage.
"""

from dataclasses import dataclass

from app.logging import get_logger
from app.services.redis_client import get_redis

log = get_logger(__name__)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    remaining: int
    retry_after_seconds: int


async def hit(identity: str, *, limit: int, window_seconds: int) -> Verdict:
    redis = get_redis()
    key = f"ratelimit:{identity}"

    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        # Only the first increment sets the expiry. `EXPIRE ... NX` keeps the
        # window anchored to the first request; expiring unconditionally would
        # push the deadline forward on every call and the window would never
        # close for a caller who keeps knocking.
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    except Exception as exc:  # pragma: no cover - exercised by killing Redis
        log.warning("rate_limit_unavailable", error=type(exc).__name__)
        return Verdict(allowed=True, remaining=limit, retry_after_seconds=0)

    count = int(count)
    ttl = int(ttl)
    if ttl < 0:
        ttl = window_seconds

    if count > limit:
        return Verdict(allowed=False, remaining=0, retry_after_seconds=ttl)
    return Verdict(allowed=True, remaining=limit - count, retry_after_seconds=0)


async def reset(identity: str) -> None:
    """Used by tests, so one test's requests do not exhaust another's budget."""
    await get_redis().delete(f"ratelimit:{identity}")
