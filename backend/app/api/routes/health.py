"""Health endpoints.

Two on purpose. /health/live answers "is this process running" and must never
touch a dependency — it is what an orchestrator restarts on. /health answers
"can this process do its job", which means checking Postgres and Redis, and is
what a human or a status page reads.
"""

from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.config import settings
from app.db import check_database
from app.services.redis_client import check_redis

router = APIRouter(tags=["health"])


class DependencyStatus(BaseModel):
    ok: bool
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    dependencies: dict[str, DependencyStatus]


@router.get("/health/live", summary="Liveness — is the process up")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse, summary="Readiness — can it work")
async def health(response: Response) -> dict[str, Any]:
    checks = {
        "database": await check_database(),
        "redis": await check_redis(),
    }
    healthy = all(c["ok"] for c in checks.values())

    # 503 so a load balancer takes the instance out of rotation rather than
    # sending traffic to a process that cannot reach its database.
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else "degraded",
        "version": "0.1.0",
        "environment": settings.environment,
        "dependencies": checks,
    }
