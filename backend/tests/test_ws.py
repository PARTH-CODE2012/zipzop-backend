"""The WebSocket — contract §8.

What is worth asserting is the *contract around* the socket, not the socket:
that a bad ticket is refused with a code the client can act on, that an event
published by a worker reaches a browser subscribed on a different process, and
— since M7 — that the socket is opened by a spent-on-use ticket rather than the
access token (M7-19) and dies with the token that asked for it (M7-18).

**Nothing here is the source of truth.** Every event carries only what a client
would need to know that something changed — `job.succeeded` deliberately has no
result payload, because results have exactly one delivery path (`GET /jobs/{id}`)
whether the socket was connected or not.
"""

import json
import secrets
import time
import uuid

import pytest
import sqlalchemy as sa
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.ws import TOKEN_EXPIRED_CLOSE
from app.main import create_app
from app.models import User, UserStatus
from app.services import security, ws_tickets
from app.services.redis_client import user_channel

V1 = "/v1"


@pytest.fixture
def app_client() -> TestClient:
    # No database dependency: the socket authenticates from the ticket alone and
    # then only talks to Redis.
    return TestClient(create_app())


def test_a_missing_ticket_is_refused_with_a_reason(app_client: TestClient) -> None:
    """Closed with 1008 *after* accepting, not by failing the handshake.

    A bare handshake failure reaches the browser as an unexplained error, and
    the client cannot tell "that ticket is no good" from "the server is down".
    The close code is what makes that distinguishable.
    """
    with (
        app_client.websocket_connect("/v1/ws") as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_text()
    assert closed.value.code == 1008


def test_a_forged_ticket_is_refused(app_client: TestClient) -> None:
    with (
        app_client.websocket_connect(f"/v1/ws?ticket={secrets.token_urlsafe(32)}") as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_text()
    assert closed.value.code == 1008


def test_an_access_token_in_the_url_no_longer_opens_the_socket(app_client: TestClient) -> None:
    """M7-19. A valid access token was the socket's credential, so every access
    log that recorded request lines held live 15-minute bearer tokens. Neither
    the old parameter nor the token in the new one may open anything now."""
    token, _ = security.issue_access_token(uuid.uuid4())

    for url in (f"/v1/ws?token={token}", f"/v1/ws?ticket={token}"):
        with (
            app_client.websocket_connect(url) as socket,
            pytest.raises(WebSocketDisconnect) as closed,
        ):
            socket.receive_text()
        assert closed.value.code == 1008, url


def test_a_ticket_opens_one_socket_and_only_one(app_client: TestClient) -> None:
    """Spent by the read. A ticket a log recorded, or one replayed by anything
    that saw the URL, is worth nothing once the browser has used it."""
    ticket = _plant_ticket(uuid.uuid4())

    # The handshake completing is the redeem having happened: the handler
    # spends the ticket before it accepts.
    with app_client.websocket_connect(f"/v1/ws?ticket={ticket}"):
        with (
            app_client.websocket_connect(f"/v1/ws?ticket={ticket}") as replay,
            pytest.raises(WebSocketDisconnect) as closed,
        ):
            replay.receive_text()
        assert closed.value.code == 1008


def test_the_socket_closes_when_the_token_behind_it_expires(app_client: TestClient) -> None:
    """M7-18. The socket used to stream for as long as the connection held,
    whatever happened to the token or the account. It now closes at the
    minting token's `exp` with 4001, which the client answers with a fresh
    ticket — through the refresh, which a disabled account cannot pass."""
    ticket = _plant_ticket(uuid.uuid4(), expires_at=int(time.time()) + 2)

    started = time.monotonic()
    with (
        app_client.websocket_connect(f"/v1/ws?ticket={ticket}") as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        for _ in range(10):
            socket.receive_text()
    assert closed.value.code == TOKEN_EXPIRED_CLOSE
    assert time.monotonic() - started < 10


def test_a_ticket_past_its_tokens_expiry_is_refused(app_client: TestClient) -> None:
    ticket = _plant_ticket(uuid.uuid4(), expires_at=int(time.time()) - 1)
    with (
        app_client.websocket_connect(f"/v1/ws?ticket={ticket}") as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_text()
    assert closed.value.code == 1008


def test_an_event_published_for_a_user_reaches_their_socket(app_client: TestClient) -> None:
    """The fan-out: a worker publishes to `user:{id}` knowing nothing about
    which replica holds the socket, and the subscribed one forwards it."""
    user_id = uuid.uuid4()
    ticket = _plant_ticket(user_id)

    with app_client.websocket_connect(f"/v1/ws?ticket={ticket}") as socket:
        # The subscription is established inside the handler, so publishing
        # immediately would race it. `redis` returns the number of subscribers,
        # which is how we know it is listening rather than how long we waited.
        redis_sync_ok = False
        for _ in range(50):
            delivered = _publish_sync(
                user_id, {"type": "job.progress", "jobId": "job_x", "progress": 62}
            )
            if delivered:
                redis_sync_ok = True
                break
            time.sleep(0.05)
        assert redis_sync_ok, "nobody was subscribed to the user's channel"

        # A heartbeat may arrive first; the event is what we are waiting for.
        for _ in range(5):
            message = json.loads(socket.receive_text())
            if message.get("type") != "ping":
                break
        assert message["type"] == "job.progress"
        assert message["jobId"] == "job_x"
        assert message["progress"] == 62


def test_one_users_events_never_reach_another(app_client: TestClient) -> None:
    """The channel is per user. A shared one would leak every job's progress —
    including which tools somebody runs and how often — to everyone online."""
    listener = uuid.uuid4()
    stranger = uuid.uuid4()
    ticket = _plant_ticket(listener)

    with app_client.websocket_connect(f"/v1/ws?ticket={ticket}") as socket:
        for _ in range(50):
            if _publish_sync(listener, {"type": "job.progress", "jobId": "job_mine"}):
                break
            time.sleep(0.05)
        # Published after ours, so if channels leaked it would arrive second and
        # the assertion below would see it. Waiting on a timeout would prove the
        # same thing far more slowly and far less reliably.
        _publish_sync(stranger, {"type": "job.succeeded", "jobId": "job_secret"})

        seen = []
        for _ in range(4):
            message = json.loads(socket.receive_text())
            if message.get("type") == "ping":
                continue
            seen.append(message.get("jobId"))
            break

        assert seen == ["job_mine"]


# --------------------------------------------------------------------------
# POST /ws/ticket
# --------------------------------------------------------------------------


async def test_a_ticket_needs_a_signed_in_account(client: AsyncClient) -> None:
    assert (await client.post(f"{V1}/ws/ticket")).status_code == 401


async def test_a_ticket_is_minted_for_the_caller_and_dies_with_their_token(
    client: AsyncClient,
) -> None:
    headers, user_id = await _account(client)
    token = headers["Authorization"].removeprefix("Bearer ")

    response = await client.post(f"{V1}/ws/ticket", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["expiresIn"] == ws_tickets.TICKET_TTL_SECONDS
    ticket = body["ticket"]
    assert ticket not in token and len(ticket) >= 40

    # Stored as a digest: the raw ticket is not a key anyone can find in Redis.
    import redis as sync_redis

    from app.config import settings

    raw = sync_redis.from_url(settings.redis_url, decode_responses=True)
    try:
        assert raw.get(f"ws_ticket:{ticket}") is None
        stored = json.loads(raw.get(ws_tickets.storage_key(ticket)) or "{}")
        ttl = raw.ttl(ws_tickets.storage_key(ticket))
    finally:
        raw.close()
    assert stored["sub"] == str(user_id)
    assert stored["exp"] == security.access_token_expiry(token)
    assert 0 < ttl <= ws_tickets.TICKET_TTL_SECONDS


async def test_a_suspended_account_cannot_mint_a_ticket(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The socket's expiry only closes the hole if a fresh ticket cannot be had
    by an account that should no longer have one."""
    headers, user_id = await _account(client)
    await db.execute(sa.update(User).where(User.id == user_id).values(status=UserStatus.SUSPENDED))
    await db.flush()

    assert (await client.post(f"{V1}/ws/ticket", headers=headers)).status_code == 403


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={"email": f"{uuid.uuid4().hex[:12]}@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    token = body["accessToken"]
    return {"Authorization": f"Bearer {token}"}, security.read_access_token(token)


def _plant_ticket(user_id: uuid.UUID, *, expires_at: int | None = None) -> str:
    """A ticket written straight into Redis, the way `POST /ws/ticket` writes it.

    The socket tests run the app on the TestClient's own loop with no database,
    so they mint tickets here rather than through the route — which has tests
    of its own above.
    """
    import redis as sync_redis

    from app.config import settings

    ticket = secrets.token_urlsafe(32)
    client = sync_redis.from_url(settings.redis_url)
    try:
        client.set(
            ws_tickets.storage_key(ticket),
            ws_tickets.encode(user_id, expires_at or int(time.time()) + 900),
            ex=ws_tickets.TICKET_TTL_SECONDS,
        )
    finally:
        client.close()
    return ticket


def _publish_sync(user_id: uuid.UUID, payload: dict[str, object]) -> int:
    """Publish from the synchronous test, on its own connection.

    The async client the app uses belongs to the app's event loop; borrowing it
    from here is the "attached to a different loop" fault the worker session
    docstring describes at length.
    """
    import redis as sync_redis

    from app.config import settings

    client = sync_redis.from_url(settings.redis_url)
    try:
        return int(client.publish(user_channel(str(user_id)), json.dumps(payload)))
    finally:
        client.close()
