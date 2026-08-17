"""Shared test fixtures.

These tests run against **real infrastructure** — a real Postgres, a real
MinIO, real ffmpeg. Nothing here is mocked, because the things M2 gets wrong
are exactly the things a mock cannot reproduce: a presigned signature that does
not verify, a partial index that does not fire, an ffprobe field that is a
string on one container and a number on another.

The cost is that `make test-backend` needs those services up. The alternative
costs more: a green suite that proves the mock works.

Environment is set before the first `app.*` import — `app.config` builds its
settings singleton at module import time, so anything set afterwards is too
late.
"""

import os
import pathlib
import subprocess
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any

# --------------------------------------------------------------------------
# Environment. `setdefault`, so CI's own values win where it sets them.
# --------------------------------------------------------------------------
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://zipzop:zipzop@localhost:5432/zipzop_test"
)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-used-anywhere-real")
os.environ.setdefault("S3_BUCKET", "zipzop-media-test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db import get_session
from app.main import create_app

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


def _guard_test_database() -> str:
    """Refuse to run against anything that is not obviously a test database.

    The session fixture below truncates every table between tests. Pointed at
    a development database by a stray environment variable, that is a very fast
    way to lose an afternoon's data.
    """
    url = sa.engine.make_url(settings.database_url)
    name = url.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run tests against database {name!r}: "
            "the name must end with '_test'. Set DATABASE_URL."
        )
    return name


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    """Create the test database if absent, then migrate it to head.

    Migrations rather than `create_all`: a schema built from the models would
    pass tests the real migration chain fails, which is the one thing this
    fixture must not allow.
    """
    name = _guard_test_database()

    admin_url = sa.engine.make_url(settings.sync_database_url).set(database="postgres")
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        env={**os.environ},
    )
    yield


@pytest.fixture(scope="session")
async def engine(database: None) -> AsyncGenerator[Any, None]:
    eng = create_async_engine(settings.database_url, poolclass=sa.pool.NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """A session whose every write is rolled back when the test ends.

    The outer transaction is never committed. `join_transaction_mode` makes the
    session open a SAVEPOINT instead of a real transaction, so application code
    can call `commit()` — which it does, in `get_session` — without escaping
    the rollback.
    """
    conn = await engine.connect()
    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


@pytest.fixture(autouse=True)
async def _fresh_rate_limits() -> AsyncGenerator[None, None]:
    """Clear the rate-limit counters between tests.

    Every test drives the API from the same client address, and the auth
    routes allow 20 requests a minute. Without this the twenty-first test in a
    file fails with a 429 that has nothing to do with what it is testing.

    Redis database 9 is set aside for tests by the `REDIS_URL` above, so
    flushing it cannot touch development data.
    """
    from app.services.redis_client import get_redis

    await get_redis().flushdb()
    yield


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client whose requests share the test's rolled-back session."""
    app = create_app()

    async def _session_override() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_session] = _session_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Object storage
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def s3() -> Any:
    """A boto3 client against the local MinIO, with the test bucket created.

    Path-style addressing: MinIO does not serve virtual-host style on
    `localhost`, and a signature computed for the wrong style verifies as a
    403 that looks like bad credentials.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
        ),
    )
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
    return client


@pytest.fixture
def s3_prefix() -> str:
    """A unique key prefix per test, so tests cannot see each other's objects."""
    return f"t{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def media_dir() -> pathlib.Path:
    d = BACKEND_ROOT / ".pytest-media"
    d.mkdir(exist_ok=True)
    return d


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def sample_video(media_dir: pathlib.Path) -> pathlib.Path:
    """A 4-second 640x360 25 fps clip with a 440 Hz tone.

    Small on purpose: every ingest test pays for this file's decode. The
    dimensions are deliberately not 480-tall, so a proxy that forgot to scale
    is visible in the assertion.
    """
    path = media_dir / "sample.mp4"
    if not path.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(path),
        )
    return path


@pytest.fixture(scope="session")
def silent_video(media_dir: pathlib.Path) -> pathlib.Path:
    """The same, with no audio track at all — peaks extraction must not crash."""
    path = media_dir / "silent.mp4"
    if not path.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            str(path),
        )
    return path


@pytest.fixture(scope="session")
def not_a_video(media_dir: pathlib.Path) -> pathlib.Path:
    """Bytes that are not media. ffprobe must fail and the API must say why."""
    path = media_dir / "broken.mp4"
    if not path.exists():
        path.write_bytes(b"this is not an mp4, it is a sentence" * 64)
    return path
