"""Shared test fixtures.

These tests do not need a database or Redis. Health checks report dependency
failures rather than raising, so the app boots and answers with everything
down — which is exactly what makes the skeleton testable in CI before any
service exists.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
