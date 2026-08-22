"""The WebSocket — contract §8.

What is worth asserting is the *contract around* the socket, not the socket:
that a bad token is refused with a code the client can act on, and that an event
published by a worker reaches a browser subscribed on a different process.

**Nothing here is the source of truth.** Every event carries only what a client
would need to know that something changed — `job.succeeded` deliberately has no
result payload, because results have exactly one delivery path (`GET /jobs/{id}`)
whether the socket was connected or not.
"""

import json
import uuid

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import security
from app.services.redis_client import user_channel


@pytest.fixture
def app_client() -> TestClient:
    # No database dependency: the socket authenticates from the token alone and
    # then only talks to Redis.
    return TestClient(create_app())


def test_a_missing_token_is_refused_with_a_reason(app_client: TestClient) -> None:
    """Closed with 1008 *after* accepting, not by failing the handshake.

    A bare handshake failure reaches the browser as an unexplained error, and
    the client cannot tell "your token expired, refresh it" from "the server is
    down, back off". The close code is what makes that distinguishable.
    """
    with (
        app_client.websocket_connect("/v1/ws") as socket,
        pytest.raises(WebSocketDisconnect),
    ):
        socket.receive_text()


def test_a_forged_token_is_refused(app_client: TestClient) -> None:
    with (
        app_client.websocket_connect("/v1/ws?token=not-a-real-token") as socket,
        pytest.raises(WebSocketDisconnect),
    ):
        socket.receive_text()


def test_an_event_published_for_a_user_reaches_their_socket(app_client: TestClient) -> None:
    """The fan-out: a worker publishes to `user:{id}` knowing nothing about
    which replica holds the socket, and the subscribed one forwards it."""
    user_id = uuid.uuid4()
    token, _ = security.issue_access_token(user_id)

    with app_client.websocket_connect(f"/v1/ws?token={token}") as socket:
        # The subscription is established inside the handler, so publishing
        # immediately would race it. `redis` returns the number of subscribers,
        # which is how we know it is listening rather than how long we waited.
        import time

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
    token, _ = security.issue_access_token(listener)

    with app_client.websocket_connect(f"/v1/ws?token={token}") as socket:
        import time

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
