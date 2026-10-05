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
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.deps import CurrentUser, general_rate_limit
from app.api.schemas.common import ApiModel
from app.logging import get_logger
from app.services import ws_tickets
from app.services.redis_client import get_redis, new_connection, user_channel
from app.services.security import access_token_expiry

log = get_logger(__name__)

router = APIRouter(tags=["ws"])

#: Sent when the client has been quiet, so a proxy between us does not decide
#: the connection is dead. Browsers answer a ping frame themselves, but many
#: load balancers only count *data*, so this is a real message.
HEARTBEAT_SECONDS = 25


#: Close code for "the token behind this socket has expired". In the 4000-4999
#: range the protocol leaves to applications; the client answers it by asking
#: for a fresh ticket, where 1008 means "do not retry with the same thing".
TOKEN_EXPIRED_CLOSE = 4001


class WsTicketResponse(ApiModel):
    ticket: str
    #: Seconds the ticket can wait before the handshake that spends it.
    expires_in: int


@router.post(
    "/ws/ticket",
    response_model=WsTicketResponse,
    summary="A one-time ticket to open the event socket",
    dependencies=[Depends(general_rate_limit)],
)
async def ws_ticket(request: Request, user: CurrentUser) -> WsTicketResponse:
    """Spent by the handshake within 30 seconds, and only once.

    `CurrentUser` has already verified the bearer token and refused an account
    that is not active, so a ticket is only ever minted for a live session. The
    socket it opens closes when that token expires (M7-18).
    """
    token = request.headers["authorization"].partition(" ")[2]
    ticket = await ws_tickets.issue(
        get_redis(), user_id=user.id, expires_at=access_token_expiry(token)
    )
    return WsTicketResponse(ticket=ticket, expires_in=ws_tickets.TICKET_TTL_SECONDS)


@router.websocket("/ws")
async def events(websocket: WebSocket, ticket: str = Query(default="")) -> None:
    """`wss://…/v1/ws?ticket=<ticket from POST /ws/ticket>`.

    **A ticket, not the access token (M7-19).** Browsers cannot set headers on
    a WebSocket handshake, so the credential has to be in the URL — and URLs
    end up in access logs. Until M7 this was the access token itself: a live
    bearer credential for every HTTP route, in every log line that recorded a
    request line. A ticket is single-use, lives thirty seconds and opens
    nothing but this socket, so the one a log records is already spent.
    """
    # Its own connection, not the shared pool - see `new_connection`. Opened
    # before the ticket is redeemed so the redeem and the subscription share
    # the handler's own event loop.
    redis = new_connection()
    redeemed = await ws_tickets.redeem(redis, ticket)
    if redeemed is None:
        await redis.aclose()
        # 1008 = policy violation. Closing *before* accepting would give the
        # browser a bare handshake failure with no reason in it; accepting and
        # then closing with a code is what lets the client tell "that ticket is
        # no good" from "the server is down" and back off instead of hammering.
        await websocket.accept()
        await websocket.close(code=1008, reason="invalid or expired ticket")
        return

    user_id = str(redeemed.user_id)
    await websocket.accept()
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    log.info("ws_connected", user_id=user_id)

    try:
        # The socket is worth exactly what the token that asked for it was
        # worth, and for exactly as long (M7-18).
        async with asyncio.timeout(max(0.0, redeemed.expires_at - time.time())):
            await asyncio.gather(
                _forward(websocket, pubsub),
                _drain(websocket),
            )
    except TimeoutError:
        if websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close(code=TOKEN_EXPIRED_CLOSE, reason="token expired")
        log.info("ws_expired", user_id=user_id)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("ws_failed", user_id=user_id, error=type(exc).__name__)
    finally:
        await pubsub.unsubscribe(user_channel(user_id))
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await redis.aclose()
        # Our side's state, not the client's: after a close *we* sent, the
        # client is still "connected" until it answers, and a second close
        # from here raises.
        if websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close()
        log.info("ws_closed", user_id=user_id)


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
