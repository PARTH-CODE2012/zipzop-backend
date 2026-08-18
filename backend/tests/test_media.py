"""Uploads and the media bin — contract §3.

These talk to a real MinIO. The presigned URLs are actually used: a signature
that does not verify, a path-style mismatch, a `Content-Type` that is not part
of the signature — none of those can be caught by asserting on a URL string,
and all of them are 403s in a browser.
"""

import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.config import settings
from app.models import AssetKind, AssetStatus, MediaAsset, Project
from app.models.project import ProjectAsset

V1 = "/v1"
pytestmark = pytest.mark.storage


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={
                "email": f"{uuid.uuid4().hex[:12]}@example.com",
                "password": "hunter2hunter2",
                "displayName": "Sam",
            },
        )
    ).json()
    headers = {"Authorization": f"Bearer {body['accessToken']}"}
    return headers, uuid.UUID(body["user"]["id"].removeprefix("usr_"))


async def _reserve(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    size_bytes: int,
    content_type: str = "video/mp4",
    filename: str = "clip.mp4",
    idempotency_key: str | None = None,
) -> httpx.Response:
    extra = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return await client.post(
        f"{V1}/media/uploads",
        headers={**headers, **extra},
        json={"filename": filename, "sizeBytes": size_bytes, "contentType": content_type},
    )


async def _put(url: str, payload: bytes, content_type: str) -> int:
    """Upload exactly as a browser would: no Authorization header, and the
    Content-Type the API told us to send."""
    async with httpx.AsyncClient(timeout=60) as raw:
        response = await raw.put(url, content=payload, headers={"Content-Type": content_type})
        return response.status_code


async def _upload(
    client: AsyncClient, headers: dict[str, str], payload: bytes, *, filename: str = "clip.mp4"
) -> str:
    """Reserve, PUT, complete. Returns the public asset id."""
    reserved = (await _reserve(client, headers, size_bytes=len(payload), filename=filename)).json()
    assert await _put(reserved["uploadUrl"], payload, "video/mp4") == 200
    completed = await client.post(
        f"{V1}/media/{reserved['assetId']}/complete", headers=headers, json={"etag": None}
    )
    assert completed.status_code == 202, completed.text
    return str(reserved["assetId"])


# --------------------------------------------------------------------------
# Reserving an upload
# --------------------------------------------------------------------------


async def test_uploads_returns_a_presigned_url_that_actually_works(
    client: AsyncClient, s3: Any, sample_video: Any
) -> None:
    headers, _ = await _account(client)
    payload = sample_video.read_bytes()

    response = await _reserve(client, headers, size_bytes=len(payload))
    assert response.status_code == 201
    body = response.json()

    assert body["assetId"].startswith("ast_")
    assert body["method"] == "PUT"
    assert body["headers"]["Content-Type"] == "video/mp4"
    assert body["multipart"] is None

    # The point of the test: the signature verifies against the real server.
    assert await _put(body["uploadUrl"], payload, "video/mp4") == 200


async def test_the_upload_url_signature_covers_the_content_type(
    client: AsyncClient, s3: Any
) -> None:
    """`Content-Type` is signed, so sending a different one is a 403.

    This is why the contract returns `headers` rather than leaving the client
    to guess — a browser that sets its own type gets an opaque failure.
    """
    headers, _ = await _account(client)
    body = (await _reserve(client, headers, size_bytes=10)).json()
    assert await _put(body["uploadUrl"], b"0123456789", "application/octet-stream") == 403


async def test_an_unsupported_type_is_refused_before_presigning(client: AsyncClient) -> None:
    """Rejected in a round trip rather than after two gigabytes have moved."""
    headers, _ = await _account(client)
    response = await _reserve(client, headers, size_bytes=100, content_type="application/pdf")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"
    assert "video/mp4" in response.json()["error"]["details"]["supported"]


async def test_a_file_over_the_limit_is_refused(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await _reserve(client, headers, size_bytes=settings.max_upload_bytes + 1)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_going_over_the_storage_quota_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """402 STORAGE_QUOTA_EXCEEDED with usedBytes and limitBytes, per §9."""
    from app.models import PlanCode
    from app.services.plans import STORAGE_QUOTA_BYTES

    headers, user_id = await _account(client)
    free_limit = STORAGE_QUOTA_BYTES[PlanCode.FREE]

    # A row that already fills the account.
    db.add(
        MediaAsset(
            user_id=user_id,
            kind=AssetKind.VIDEO,
            status=AssetStatus.READY,
            storage_key="originals/x/y/source.mp4",
            size_bytes=free_limit,
        )
    )
    await db.flush()

    response = await _reserve(client, headers, size_bytes=1024)
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    details = response.json()["error"]["details"]
    assert details["usedBytes"] == free_limit
    assert details["limitBytes"] == free_limit


async def test_replaying_an_idempotency_key_reserves_nothing_new(
    client: AsyncClient, db: AsyncSession
) -> None:
    """contract §1: a retry after a network timeout is indistinguishable from a
    second request, so the key has to make them the same request."""
    headers, user_id = await _account(client)
    key = str(uuid.uuid4())

    first = (await _reserve(client, headers, size_bytes=1000, idempotency_key=key)).json()
    second = (await _reserve(client, headers, size_bytes=1000, idempotency_key=key)).json()
    assert first["assetId"] == second["assetId"]

    rows = await db.scalar(
        sa.select(sa.func.count()).select_from(MediaAsset).where(MediaAsset.user_id == user_id)
    )
    assert rows == 1


async def test_a_large_file_is_offered_multipart(client: AsyncClient, s3: Any) -> None:
    """Over 100 MB the contract carries per-part URLs instead.

    A single PUT of a 2 GB file over a phone connection is one timeout away
    from starting again from zero.
    """
    headers, _ = await _account(client)
    size = settings.multipart_threshold_bytes + 1
    body = (await _reserve(client, headers, size_bytes=size)).json()

    assert body["multipart"] is not None
    plan = body["multipart"]
    assert plan["partSizeBytes"] >= 8 * 1024 * 1024
    expected_parts = -(-size // plan["partSizeBytes"])
    assert len(plan["parts"]) == expected_parts
    assert plan["parts"][0]["partNumber"] == 1


async def test_a_filename_cannot_escape_its_key_prefix(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The filename is user input. A path separator in it must not put the
    object somewhere else in the bucket."""
    headers, user_id = await _account(client)
    body = (
        await _reserve(client, headers, size_bytes=10, filename="../../../../etc/passwd.mp4")
    ).json()

    asset = await db.get(MediaAsset, uuid.UUID(body["assetId"].removeprefix("ast_")))
    assert asset is not None
    assert asset.storage_key.startswith(f"originals/{user_id}/")
    assert ".." not in asset.storage_key
    assert asset.original_filename == "passwd.mp4"


# --------------------------------------------------------------------------
# Completing an upload
# --------------------------------------------------------------------------


async def test_complete_verifies_the_object_is_really_there(client: AsyncClient, s3: Any) -> None:
    """A client saying it finished is not evidence that it did."""
    headers, _ = await _account(client)
    reserved = (await _reserve(client, headers, size_bytes=100)).json()

    response = await client.post(
        f"{V1}/media/{reserved['assetId']}/complete", headers=headers, json={"etag": None}
    )
    assert response.status_code == 404


async def test_complete_rejects_an_object_of_the_wrong_size(client: AsyncClient, s3: Any) -> None:
    """Reserving one byte against the quota and uploading a gigabyte must not
    work."""
    headers, _ = await _account(client)
    reserved = (await _reserve(client, headers, size_bytes=10)).json()
    assert await _put(reserved["uploadUrl"], b"x" * 5000, "video/mp4") == 200

    response = await client.post(
        f"{V1}/media/{reserved['assetId']}/complete", headers=headers, json={"etag": None}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"announcedBytes": 10, "actualBytes": 5000}

    # The half of the property this test used to describe and not check. The
    # rejected reservation has to be *gone*: left behind it counts 10 bytes
    # against the quota while 5,000 sit in storage, which is the evasion the
    # size check exists to stop. It survived until 18 August because the
    # rollback that discarded the delete was invisible to the test harness.
    after = await client.get(f"{V1}/media/{reserved['assetId']}", headers=headers)
    assert after.status_code == 404


async def test_complete_twice_is_harmless(
    client: AsyncClient, s3: Any, sample_video: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicated call from a flaky network returns the asset rather than an
    error."""
    _silence_celery(monkeypatch)
    headers, _ = await _account(client)
    payload = sample_video.read_bytes()
    asset_id = await _upload(client, headers, payload)

    again = await client.post(
        f"{V1}/media/{asset_id}/complete", headers=headers, json={"etag": None}
    )
    assert again.status_code == 202


def _silence_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop `complete` from enqueueing to a broker no test is draining.

    Ingest itself is tested by calling the pipeline directly — see
    tests/test_ingest.py — so the only thing lost here is the `.delay()` call.
    """
    from app.workers.tasks import ingest as task_module

    monkeypatch.setattr(task_module.process_asset, "delay", lambda *a, **k: None)


# --------------------------------------------------------------------------
# Reading media back
# --------------------------------------------------------------------------


async def test_signed_urls_are_returned_and_fetchable(
    client: AsyncClient, db: AsyncSession, s3: Any, sample_video: Any
) -> None:
    """contract §3: three signed URLs, one hour. The test fetches them, because
    a URL that is merely present proves nothing."""
    headers, user_id = await _account(client)

    proxy_key = f"proxies/{user_id}/probe/proxy.mp4"
    s3.put_object(Bucket=settings.s3_bucket, Key=proxy_key, Body=b"not-really-a-video")

    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/source.mp4",
        proxy_key=proxy_key,
        duration_ms=4000,
    )
    db.add(asset)
    await db.flush()

    body = (
        await client.get(f"{V1}/media/{ids.encode(ids.ASSET, asset.id)}", headers=headers)
    ).json()
    assert body["proxyUrl"] is not None
    assert body["thumbnailUrl"] is None  # no key, no URL

    async with httpx.AsyncClient(timeout=30) as raw:
        fetched = await raw.get(body["proxyUrl"])
    assert fetched.status_code == 200
    assert fetched.content == b"not-really-a-video"


async def test_an_unsigned_url_is_refused(client: AsyncClient, db: AsyncSession, s3: Any) -> None:
    """docs/03 §6.3: *"Everything is private."*

    If this ever fails, the bucket has been made public and every proxy in the
    system is readable by anyone who can guess a UUID.
    """
    _, user_id = await _account(client)
    key = f"proxies/{user_id}/private/proxy.mp4"
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"secret")

    # Built from the endpoint and the bucket actually in use, not from
    # CDN_BASE_URL — that setting names the production bucket and would 404
    # here for the wrong reason, turning a real security assertion into a
    # test that passes by accident.
    direct = f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"
    async with httpx.AsyncClient(timeout=30) as raw:
        response = await raw.get(direct)
    assert response.status_code in (401, 403), (
        f"{direct} answered {response.status_code}: the bucket is readable without a signature"
    )


async def test_media_is_listed_newest_first_and_pages_by_cursor(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    for index in range(5):
        db.add(
            MediaAsset(
                user_id=user_id,
                kind=AssetKind.VIDEO,
                status=AssetStatus.READY,
                storage_key=f"originals/x/{index}/source.mp4",
                original_filename=f"{index}.mp4",
            )
        )
    await db.flush()

    first = (await client.get(f"{V1}/media?limit=2", headers=headers)).json()
    assert len(first["items"]) == 2
    assert first["nextCursor"]

    second = (
        await client.get(f"{V1}/media?limit=2&cursor={first['nextCursor']}", headers=headers)
    ).json()
    assert len(second["items"]) == 2

    seen = {item["id"] for item in first["items"]} | {item["id"] for item in second["items"]}
    assert len(seen) == 4  # no repeats across the page boundary

    last = (
        await client.get(f"{V1}/media?limit=2&cursor={second['nextCursor']}", headers=headers)
    ).json()
    assert len(last["items"]) == 1
    assert last["nextCursor"] is None


async def test_a_bad_cursor_is_a_validation_error_not_a_crash(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.get(f"{V1}/media?cursor=not-base64-at-all", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# Deleting
# --------------------------------------------------------------------------


async def test_an_asset_used_by_a_project_cannot_be_deleted(
    client: AsyncClient, db: AsyncSession
) -> None:
    """409 ASSET_IN_USE, naming the projects — contract §3."""
    headers, user_id = await _account(client)
    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/s.mp4",
    )
    project = Project(user_id=user_id, title="Holiday")
    db.add_all([asset, project])
    await db.flush()
    db.add(ProjectAsset(project_id=project.id, asset_id=asset.id))
    await db.flush()

    response = await client.delete(f"{V1}/media/{ids.encode(ids.ASSET, asset.id)}", headers=headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ASSET_IN_USE"
    assert error["details"]["projectIds"] == [ids.encode(ids.PROJECT, project.id)]


async def test_deleting_an_unused_asset_hides_it(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/s.mp4",
    )
    db.add(asset)
    await db.flush()
    public_id = ids.encode(ids.ASSET, asset.id)

    assert (await client.delete(f"{V1}/media/{public_id}", headers=headers)).status_code == 204
    assert (await client.get(f"{V1}/media/{public_id}", headers=headers)).status_code == 404

    # Soft delete: the row survives so the objects can be swept later, and an
    # accidental delete has a window.
    await db.refresh(asset)
    assert asset.deleted_at is not None


# --------------------------------------------------------------------------
# Scoping — the data-leak test
# --------------------------------------------------------------------------


async def test_no_endpoint_returns_another_accounts_media(
    client: AsyncClient, db: AsyncSession
) -> None:
    """PHASE1-TASKS.md M2: *"a route that forgets is a data leak."*

    One asset, two accounts, every media endpoint. The owner sees it and the
    stranger cannot see, read, page, or delete it — and gets 404 rather than
    403, which would confirm the id exists.
    """
    owner_headers, owner_id = await _account(client)
    stranger_headers, _ = await _account(client)

    asset = MediaAsset(
        user_id=owner_id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key="originals/x/y/s.mp4",
        peaks_key="peaks/x/y/peaks.json",
    )
    db.add(asset)
    await db.flush()
    public_id = ids.encode(ids.ASSET, asset.id)

    assert (await client.get(f"{V1}/media/{public_id}", headers=owner_headers)).status_code == 200

    assert (
        await client.get(f"{V1}/media/{public_id}", headers=stranger_headers)
    ).status_code == 404
    assert (
        await client.delete(f"{V1}/media/{public_id}", headers=stranger_headers)
    ).status_code == 404
    assert (
        await client.get(f"{V1}/media/{public_id}/peaks", headers=stranger_headers)
    ).status_code == 404

    listed = (await client.get(f"{V1}/media", headers=stranger_headers)).json()
    assert listed["items"] == []


async def test_a_malformed_asset_id_is_a_404_not_a_500(client: AsyncClient) -> None:
    """Telling a malformed id apart from a hidden one would leak whether it
    exists."""
    headers, _ = await _account(client)
    for bad in ("nonsense", "ast_not-a-uuid", "prj_" + str(uuid.uuid4())):
        response = await client.get(f"{V1}/media/{bad}", headers=headers)
        assert response.status_code == 404, bad


async def test_media_endpoints_need_an_account(client: AsyncClient) -> None:
    assert (await client.get(f"{V1}/media")).status_code == 401
    assert (
        await client.post(
            f"{V1}/media/uploads",
            json={"filename": "a.mp4", "sizeBytes": 1, "contentType": "video/mp4"},
        )
    ).status_code == 401
