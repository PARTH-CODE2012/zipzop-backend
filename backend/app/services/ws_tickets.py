"""One-time tickets for opening the WebSocket.

**Why the socket does not take the access token any more (M7-19).** A browser
cannot set headers on a WebSocket handshake, so whatever authenticates the
socket rides in the URL — and a URL is what access logs, proxies and error
trackers record. With `?token=` that was a live 15-minute bearer credential in
every log line that recorded a request line, usable against every HTTP route.

A ticket is still in the URL, but it is worth nothing once read: thirty
seconds of life, redeemed exactly once (`GETDEL`), and good for nothing except
opening this one socket. A log that records it records a spent ticket.

**The socket lives no longer than the token that asked for it (M7-18).** The
ticket carries that token's `exp`, and the handler closes the socket at that
instant with `4001`, which the client answers by asking for a fresh ticket —
going through the normal refresh, which an account that has been disabled or
signed out can no longer pass. Before this, a socket opened with a token kept
streaming for as long as the connection held.

Only the SHA-256 of a ticket is stored, the way refresh tokens are: a Redis
dump or a `MONITOR` session shows keys nobody can present.
"""

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

#: Long enough for a slow phone to go from the HTTP response to the handshake,
#: short enough that a ticket copied out of a log is already dead.
TICKET_TTL_SECONDS = 30

_PREFIX = "ws_ticket:"


@dataclass(frozen=True)
class Ticket:
    user_id: uuid.UUID
    #: Unix time at which the socket must close — the minting token's `exp`.
    expires_at: int


def storage_key(ticket: str) -> str:
    return _PREFIX + hashlib.sha256(ticket.encode()).hexdigest()


def encode(user_id: uuid.UUID, expires_at: int) -> str:
    return json.dumps({"sub": str(user_id), "exp": int(expires_at)})


async def issue(redis: Any, *, user_id: uuid.UUID, expires_at: int) -> str:
    ticket = secrets.token_urlsafe(32)
    await redis.set(storage_key(ticket), encode(user_id, expires_at), ex=TICKET_TTL_SECONDS)
    return ticket


async def redeem(redis: Any, ticket: str) -> Ticket | None:
    """The ticket's owner and deadline, or None. Spends it either way.

    `GETDEL` is what makes it single-use: two handshakes racing with one ticket
    cannot both read it, because the read is the delete.
    """
    if not ticket or len(ticket) > 128:
        return None
    raw = await redis.getdel(storage_key(ticket))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        found = Ticket(user_id=uuid.UUID(str(data["sub"])), expires_at=int(data["exp"]))
    except (ValueError, KeyError, TypeError):
        return None
    if found.expires_at <= time.time():
        return None
    return found
