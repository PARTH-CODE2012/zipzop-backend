"""The WebSocket — contract §8.

**An optimisation, not the source of truth.** Every event that arrives here
describes a change already written to a row, and every one of them is readable
from `GET /jobs/{id}` or `GET /jobs?status=running`. A client that never opens
this socket is slower and completely correct; a client that treats it as the
only delivery path is broken the first time a train goes into a tunnel.

That is why `job.succeeded` carries no result. One delivery path for results,
whether the socket was connected or not (§8).

The fan-out is one Redis channel per user. Any API replica can hold any user's
socket, and the worker that publishes knows nothing about which — it publishes
to `user:{id}` and whichever replica is subscribed forwards it.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.logging import get_logger
from app.services.redis_client import new_connection, user_channel
from app.services.security import read_access_token

log = get_logger(__name__)

router = APIRouter(tags=["ws"])

#: Sent when the client has been quiet, so a proxy between us does not decide
#: the connection is dead. Browsers answer a ping frame themselves, but many
#: load balancers only count *data*, so this is a real message.
HEARTBEAT_SECONDS = 25


@router.websocket("/ws")
async def events(websocket: WebSocket, token: str = Query(default="")) -> None:
    """`wss://…/v1/ws?token=<access_token>`.

    The token is in the query string because **browsers cannot set headers on a
    WebSocket handshake** — there is no way to send `Authorization` from
    `new WebSocket(...)`. It is an access token, so it is short-lived, and the
    connection is upgraded immediately rather than left half-open.
    """
    try:
        user_id: uuid.UUID = read_access_token(token)
    except Exception:
        # 1008 = policy violation. Closing *before* accepting would give the
        # browser a bare handshake failure with no reason in it; accepting and
        # then closing with a code is what lets the client tell "your token
        # expired" from "the server is down" and refresh instead of retrying.
        await websocket.accept()
        await websocket.close(code=1008, reason="invalid or expired token")
        return

    await websocket.accept()
    # Its own connection, not the shared pool - see `new_connection`.
    redis = new_connection()
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(str(user_id)))
    log.info("ws_connected", user_id=str(user_id))

    try:
        await asyncio.gather(
            _forward(websocket, pubsub),
            _drain(websocket),
        )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("ws_failed", user_id=str(user_id), error=type(exc).__name__)
    finally:
        await pubsub.unsubscribe(user_channel(str(user_id)))
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await redis.aclose()
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()
        log.info("ws_closed", user_id=str(user_id))


async def _forward(websocket: WebSocket, pubsub: Any) -> None:
    """Redis to the browser, plus a heartbeat when nothing is happening."""
    while True:
        message = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS
        )
        if websocket.client_state is not WebSocketState.CONNECTED:
            return
        if message is None:
            await websocket.send_text('{"type":"ping"}')
            continue
        # Published as JSON text and forwarded verbatim. Parsing it here only to
        # re-serialise it would be work that can fail for no benefit.
        await websocket.send_text(str(message["data"]))


async def _drain(websocket: WebSocket) -> None:
    """Read and discard whatever the client sends.

    Nothing in the protocol is client-to-server — the socket exists to push. But
    a receive loop has to exist anyway: without one, a disconnect is never
    noticed and the subscription leaks until the process restarts.
    """
    while True:
        await websocket.receive_text()
