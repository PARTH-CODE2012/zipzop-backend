"""Uploads, ingest status and the media bin — contract §3.

The upload flow, in three steps, none of which move bytes through this process:

1. `POST /media/uploads` — check the quota, reserve a row, hand back a
   presigned URL good for 15 minutes.
2. `PUT <uploadUrl>` — the browser talks straight to S3. Not our API: an
   `Authorization` header here breaks the signature.
3. `POST /media/{id}/complete` — verify the object is really there and the
   right size, then enqueue ingest.

Step 3 exists because step 2 happens somewhere this service cannot see. A
client that says it finished is not evidence that it did.
"""

import json
import uuid
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.api.deps import CurrentUser, Session, general_rate_limit
from app.api.errors import (
    AssetInUseError,
    FileTooLargeError,
    NotFoundError,
    StorageQuotaExceededError,
    UnsupportedMediaError,
)
from app.api.schemas.common import Page
from app.api.schemas.media import (
    SUPPORTED_CONTENT_TYPES,
    AssetResponse,
    CompleteUploadRequest,
    MultipartPart,
    MultipartPlan,
    PeaksResponse,
    UploadRequest,
    UploadResponse,
    peaks_from_document,
)
from app.config import settings
from app.logging import get_logger
from app.models import AssetKind, AssetStatus, MediaAsset
from app.repositories.media import MediaAssetRepository
from app.repositories.user import UserRepository
from app.services import idempotency, storage
from app.services.plans import storage_quota_for

log = get_logger(__name__)

router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(general_rate_limit)])


def _serialise(asset: MediaAsset) -> AssetResponse:
    """Keys become signed URLs here and nowhere else.

    They last an hour, so this must run per request — caching the result, or
    storing it, hands out links that are dead before they are used.
    """
    return AssetResponse(
        id=ids.encode(ids.ASSET, asset.id),
        kind=asset.kind.value,
        status=asset.status.value,
        original_filename=asset.original_filename,
        size_bytes=asset.size_bytes,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        # Decimal out of the database, float on the wire: JSON has no decimal
        # type and 29.970 must not become "29.970" for a client doing maths.
        fps=float(asset.fps) if asset.fps is not None else None,
        video_codec=asset.video_codec,
        audio_codec=asset.audio_codec,
        audio_channels=asset.audio_channels,
        proxy_url=storage.presign_get(asset.proxy_key),
        thumbnail_url=storage.presign_get(asset.thumbnail_key),
        peaks_url=storage.presign_get(asset.peaks_key),
        derived_from_asset_id=(
            ids.encode(ids.ASSET, asset.derived_from_asset_id)
            if asset.derived_from_asset_id
            else None
        ),
        failure_reason=asset.failure_reason,
        created_at=asset.created_at,
    )


def _kind_for(content_type: str) -> AssetKind:
    return AssetKind.AUDIO if content_type.startswith("audio/") else AssetKind.VIDEO


def _extension(filename: str, content_type: str) -> str:
    """Trust the declared type over the name.

    A filename is user input and may have no extension, the wrong one, or a
    path separator in it. `PurePosixPath(...).name` also strips any directory
    component, so `../../etc/passwd.mp4` cannot escape the key prefix.
    """
    fallback = SUPPORTED_CONTENT_TYPES[content_type]
    suffix = PurePosixPath(filename).name.rsplit(".", 1)
    if len(suffix) == 2 and 1 <= len(suffix[1]) <= 5 and suffix[1].isalnum():
        return suffix[1].lower()
    return fallback


@router.post(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    response_model=UploadResponse,
    summary="Ask for somewhere to put a file",
)
async def create_upload(
    body: UploadRequest,
    user: CurrentUser,
    session: Session,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadResponse:
    assets = MediaAssetRepository(session, user.id)

    if body.content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedMediaError(
            f"We cannot read {body.content_type} files.",
            details={"supported": sorted(SUPPORTED_CONTENT_TYPES)},
        )

    if body.size_bytes > settings.max_upload_bytes:
        raise FileTooLargeError(
            "That file is larger than we accept.",
            details={"sizeBytes": body.size_bytes, "limitBytes": settings.max_upload_bytes},
        )

    subscription = await UserRepository(session).live_subscription(user.id)
    if subscription is not None:
        limit = storage_quota_for(subscription.plan)
        used = await assets.storage_bytes_used()
        if used + body.size_bytes > limit:
            raise StorageQuotaExceededError(
                "This upload would put you over your storage limit.",
                details={"usedBytes": used, "limitBytes": limit},
            )

    # A replay must not reserve a second row. The window is a day, which is
    # far longer than the 15-minute URL it hands back.
    if idempotency_key:
        seen = await idempotency.recall(str(user.id), "media-upload", idempotency_key)
        if seen:
            existing = await assets.get_visible(uuid.UUID(seen))
            if existing is not None:
                return await _upload_response(session, existing, body, replay=True)

    asset = await assets.create_pending(
        kind=_kind_for(body.content_type),
        storage_key="",  # needs the id, which the database assigns on flush
        original_filename=PurePosixPath(body.filename).name,
        mime_type=body.content_type,
        size_bytes=body.size_bytes,
    )
    asset.storage_key = storage.original_key(
        str(user.id), str(asset.id), _extension(body.filename, body.content_type)
    )
    await session.flush()

    if idempotency_key:
        await idempotency.remember(str(user.id), "media-upload", idempotency_key, str(asset.id))

    log.info("upload_reserved", asset_id=str(asset.id), size_bytes=body.size_bytes)
    return await _upload_response(session, asset, body, replay=False)


async def _upload_response(
    session: AsyncSession, asset: MediaAsset, body: UploadRequest, *, replay: bool
) -> UploadResponse:
    presigned = storage.presign_put(asset.storage_key, body.content_type)

    multipart: MultipartPlan | None = None
    if body.size_bytes > settings.multipart_threshold_bytes:
        if asset.multipart_upload_id:
            # A replay. The upload id outlives the fifteen-minute part URLs, so
            # the client gets fresh URLs for the upload it already started —
            # not a second upload. Starting one here, which this did until
            # 28 August, orphans every part already uploaded against the first:
            # they stay in the bucket, billed, with nothing pointing at them.
            plan = storage.presign_parts(
                asset.storage_key, asset.multipart_upload_id, body.size_bytes
            )
        else:
            plan = storage.start_multipart(asset.storage_key, body.content_type, body.size_bytes)
            # Written before the client is told the upload exists. An id we
            # handed out and did not store is an upload that can never be
            # completed and never be aborted.
            asset.multipart_upload_id = plan.upload_id
            await session.flush()

        multipart = MultipartPlan(
            upload_id=plan.upload_id,
            part_size_bytes=plan.part_size_bytes,
            parts=[
                MultipartPart(part_number=int(p["partNumber"]), url=str(p["url"]))
                for p in plan.parts
            ],
        )

    return UploadResponse(
        asset_id=ids.encode(ids.ASSET, asset.id),
        upload_url=presigned.url,
        method=presigned.method,
        headers=presigned.headers,
        expires_at=presigned.expires_at,
        multipart=multipart,
    )


@router.post(
    "/{asset_id}/complete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssetResponse,
    summary="Tell us the upload finished",
)
async def complete_upload(
    asset_id: str,
    body: CompleteUploadRequest,
    user: CurrentUser,
    session: Session,
) -> AssetResponse:
    assets = MediaAssetRepository(session, user.id)
    asset = await assets.get_visible(ids.decode(ids.ASSET, asset_id))
    if asset is None:
        raise NotFoundError("We have no record of that upload.")

    if asset.status is not AssetStatus.PENDING_UPLOAD:
        # Already completed. Returning the asset rather than an error makes a
        # duplicated call from a flaky network harmless.
        return _serialise(asset)

    if body.parts:
        # The upload id comes off the row, not the request. It used to be
        # `str(body.etag or "")` — a real field on the request, and entirely
        # the wrong one, so S3 was handed an ETag where it expected an upload
        # id and every multipart completion failed with `NoSuchUpload`. Found
        # by audit, 27 August 2026; `CompleteUploadRequest` never had an upload
        # id to pass in the first place.
        if not asset.multipart_upload_id:
            raise UnsupportedMediaError(
                "This upload was not started as a multipart upload.",
                details={"partsSent": len(body.parts)},
            )
        storage.complete_multipart(
            asset.storage_key,
            asset.multipart_upload_id,
            [{"partNumber": p.part_number, "etag": p.etag} for p in body.parts],
        )

    stored = storage.head(asset.storage_key)
    if stored is None:
        raise NotFoundError("That upload never arrived. Please try again.")

    if asset.size_bytes is not None and stored.size_bytes != asset.size_bytes:
        # The reservation said one size and the object is another. Trusting the
        # client here would let someone reserve a byte against their quota and
        # upload a gigabyte.
        await assets.soft_delete(asset)
        # Committed before raising, for the same reason as the token revocation
        # in `auth.refresh`: `get_session` rolls back on exception, and the
        # rejected reservation would survive. It would sit at its *announced*
        # size while the object in storage is whatever was really uploaded —
        # which is the quota evasion this check exists to stop.
        await session.commit()
        raise UnsupportedMediaError(
            "The uploaded file did not match what was announced.",
            details={"announcedBytes": asset.size_bytes, "actualBytes": stored.size_bytes},
        )

    await assets.mark_probing(asset, size_bytes=stored.size_bytes, checksum=None)

    # Committed here, **before** the enqueue — found by audit, 26 August 2026.
    # This used to send the Celery message first and let `get_session`'s
    # dependency commit afterwards, once the handler returned. A worker can
    # start reading the instant the message lands, on its own connection, and
    # a connection that started before this one committed sees the row still
    # `pending_upload` — the exact race `POST /jobs` deliberately avoids by
    # enqueueing after its own commit (see the module docstring on
    # `app/api/routes/jobs.py`). This endpoint just never got the same fix.
    await session.commit()

    from app.workers.tasks.ingest import process_asset

    try:
        process_asset.delay(str(asset.id))
    except Exception:
        # The row is already committed as `probing`. Unlike a job — where the
        # sweep can safely re-send a stuck `queued` message because `claim()`
        # makes a redundant send a no-op — there is no equivalent guarantee for
        # an asset: `MediaAsset` has no atomic claim, so
        # `pipeline_reconciliation.py` only *reports* a `probing` asset this
        # old, it does not re-enqueue it (see that module's docstring). A send
        # failure here is therefore not self-healing yet; it is loud, at
        # least, which is the reason this is caught at all rather than left to
        # the framework's own unhandled-exception log line.
        log.exception("ingest_enqueue_failed", asset_id=str(asset.id))
        raise
    log.info("ingest_enqueued", asset_id=str(asset.id))
    return _serialise(asset)


@router.get("", response_model=Page[AssetResponse], summary="The caller's media")
async def list_media(
    user: CurrentUser,
    session: Session,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[AssetResponse]:
    parsed: AssetKind | None = None
    if kind is not None:
        try:
            parsed = AssetKind(kind)
        except ValueError:
            raise UnsupportedMediaError(f"There is no media kind called {kind!r}.") from None

    rows, next_cursor = await MediaAssetRepository(session, user.id).page(
        kind=parsed, limit=limit, cursor=cursor
    )
    return Page[AssetResponse](items=[_serialise(row) for row in rows], next_cursor=next_cursor)


@router.get("/{asset_id}", response_model=AssetResponse, summary="One asset, with signed URLs")
async def get_media(asset_id: str, user: CurrentUser, session: Session) -> AssetResponse:
    asset = await MediaAssetRepository(session, user.id).get_visible(
        ids.decode(ids.ASSET, asset_id)
    )
    if asset is None:
        raise NotFoundError()
    return _serialise(asset)


@router.get(
    "/{asset_id}/peaks",
    response_model=PeaksResponse,
    summary="The waveform",
)
async def get_peaks(asset_id: str, user: CurrentUser, session: Session) -> PeaksResponse:
    asset = await MediaAssetRepository(session, user.id).get_visible(
        ids.decode(ids.ASSET, asset_id)
    )
    if asset is None or not asset.peaks_key:
        raise NotFoundError("This file has no waveform yet.")

    import anyio

    def _read() -> bytes:
        import io

        buffer = io.BytesIO()
        storage.client().download_fileobj(settings.s3_bucket, asset.peaks_key, buffer)
        return buffer.getvalue()

    # boto3 is synchronous; running it inline would block the event loop for
    # every other request on this worker while a few hundred kilobytes move.
    payload = await anyio.to_thread.run_sync(_read)
    return peaks_from_document(json.loads(payload))


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an asset",
)
async def delete_media(asset_id: str, user: CurrentUser, session: Session) -> Response:
    assets = MediaAssetRepository(session, user.id)
    asset = await assets.get_visible(ids.decode(ids.ASSET, asset_id))
    if asset is None:
        raise NotFoundError()

    used_by = await assets.projects_using(asset.id)
    if used_by:
        raise AssetInUseError(
            f"This file is used in {len(used_by)} project{'s' if len(used_by) > 1 else ''}.",
            details={"projectIds": [ids.encode(ids.PROJECT, pid) for pid in used_by]},
        )

    # Soft delete only. The objects stay until the storage lifecycle sweep
    # collects them, so an accidental delete has a window — and `originals/` is
    # never removed automatically at all (docs/03 §6.3).
    await assets.soft_delete(asset)
    log.info("asset_deleted", asset_id=str(asset.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
