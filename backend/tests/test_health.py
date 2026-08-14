"""Health and error-envelope tests.

Small, but they pin two things every later feature relies on: the app boots,
and every error comes back in the same shape.
"""

from httpx import AsyncClient


async def test_liveness_never_touches_dependencies(client: AsyncClient) -> None:
    """Liveness must answer even with Postgres and Redis unreachable.

    This is what an orchestrator restarts on. If it checked the database, a
    database blip would restart every healthy API instance at once.
    """
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_each_dependency(client: AsyncClient) -> None:
    """Readiness reports per-dependency status and never raises."""
    response = await client.get("/health")

    # 200 when everything is reachable, 503 when it is not. Both are valid
    # outcomes here — CI has services, a laptop with docker down does not.
    assert response.status_code in (200, 503)

    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert set(body["dependencies"]) == {"database", "redis"}
    for dep in body["dependencies"].values():
        assert isinstance(dep["ok"], bool)


async def test_request_id_is_echoed_back(client: AsyncClient) -> None:
    """A caller-supplied request id survives, so a trace crosses a proxy hop."""
    response = await client.get("/health/live", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"


async def test_request_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers.get("X-Request-ID")


async def test_unknown_route_uses_the_error_envelope(client: AsyncClient) -> None:
    """Framework-raised errors come back in our shape, not Starlette's.

    Clients parse one envelope, never two.
    """
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


async def test_openapi_schema_is_generated(client: AsyncClient) -> None:
    """The contract is served. This is what dump_openapi writes to disk."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "ZipZop API"
    assert "/health" in schema["paths"]
