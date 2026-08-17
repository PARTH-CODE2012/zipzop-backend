"""Idempotency keys.

Contract §1: *"Replaying a key returns the original response rather than
creating a second job. Clients must send one — a retry after a network timeout
is otherwise indistinguishable from a second request, and the user gets charged
twice."*

`POST /jobs` records its key on the `jobs` row, where a unique index enforces
it and the record has to survive as long as the job does. `POST /media/uploads`
has no such column and does not need one: an upload reservation is worthless a
day later, and the only thing worth remembering is which asset id a key already
produced. Redis with a day's TTL says exactly that and then forgets.
"""

from typing import Final

from app.services.redis_client import get_redis

TTL_SECONDS: Final = 24 * 3600


def _key(user_id: str, scope: str, idempotency_key: str) -> str:
    # Scoped by user: two accounts generating the same UUID is vanishingly
    # unlikely, but a shared namespace would let one of them read the other's
    # asset id if it ever happened.
    return f"idem:{scope}:{user_id}:{idempotency_key}"


async def remember(user_id: str, scope: str, idempotency_key: str, value: str) -> None:
    await get_redis().set(_key(user_id, scope, idempotency_key), value, ex=TTL_SECONDS)


async def recall(user_id: str, scope: str, idempotency_key: str) -> str | None:
    result = await get_redis().get(_key(user_id, scope, idempotency_key))
    return str(result) if result is not None else None
