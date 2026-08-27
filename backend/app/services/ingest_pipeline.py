"""The ingest run: fetch the original, build the four derivatives, record them.

Kept out of the Celery task on purpose. The task is a thin wrapper that owns
retries and the event loop; everything that can be wrong about ingest is in
here, where a test can call it directly against real media and a real bucket
without a broker in the way.

Ordering matters: the row is only written **after** every upload succeeds. A
`proxy_key` pointing at an object that failed to upload is worse than no proxy
at all, because the asset would read as `ready` and the editor would try to
play a 404.
"""

import hashlib
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models import AssetKind, MediaAsset
from app.repositories.media import (
    claim_for_ingest,
    fail_ingest,
    finish_ingest,
    release_ingest_claim,
)
from app.services import ingest, storage

log = get_logger(__name__)


class TransientFailureError(Exception):
    """Something on our side broke, and the same file would probably ingest
    fine on a retry — S3 unreachable, the database dropped the connection, a
    write timed out.

    Mirrors `analysis_pipeline.TransientFailureError`: the task wrapper in
    `app.workers.tasks.ingest` catches this and calls `self.retry()`, the same
    way `run_analysis` does. Everything else — a corrupt file, no video track,
    a duration over the limit — is `UnreadableMediaError` or handled inline, and
    stays a permanent, user-facing `failed`: retrying a bad file produces the
    same answer three times and only delays the message.

    **Found by audit, 26 August 2026.** Before this, `run_ingest` caught every
    exception — including this class of infrastructure blip — and immediately
    wrote `failed` with a generic message. `process_asset`'s Celery task
    declared `max_retries=2` in its decorator, but nothing in the pipeline ever
    raised in a way that reached a `self.retry()` call, so the retry policy was
    decorative: it was never exercised by any code path.
    """


class IngestUnavailableError(Exception):
    """Another worker has this asset, or it is no longer `probing`.

    Not a failure of anything: it is the ordinary answer when a Celery message
    is delivered twice, or when the pipeline sweep re-sends one for an asset
    that turned out not to be stuck after all. The task logs it and returns —
    retrying would only lose the same race again.

    `analysis_pipeline.JobUnavailableError` in everything but name.
    """


async def run_ingest(session: AsyncSession, asset_id: uuid.UUID, *, worker_id: str) -> str:
    """Returns the asset's final status, or raises `TransientFailureError`.

    An unreadable upload is a normal outcome the user has to be told about, not
    an exception for the queue to retry — retrying a corrupt file three times
    produces the same answer three times and delays the message. An
    infrastructure failure is the opposite: the file is probably fine, and the
    queue retrying it is exactly right, which is why this raises rather than
    swallows it (see `TransientFailureError` above).

    **Claims before it works, since 27 August.** Until then this read the row
    and started transcoding, and nothing stopped two workers doing that to the
    same file at once — which is why the pipeline sweep could only report a
    stuck `probing` asset rather than retry it. `claim_for_ingest` is the same
    single-UPDATE mechanism `jobs` has had since M4.
    """
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        log.warning("ingest_asset_missing", asset_id=str(asset_id))
        return "missing"

    claimed = await claim_for_ingest(session, asset_id, worker_id=worker_id)
    if claimed is None:
        # Either somebody else holds it, or it already finished. Both are
        # normal on a redelivered message; neither is worth retrying.
        await session.rollback()
        raise IngestUnavailableError(f"asset {asset_id} is claimed or no longer probing")

    # Committed **before** the work, not after. The claiming UPDATE holds a row
    # lock until this transaction ends, and ffmpeg on a 2 GB file is minutes —
    # without this a second worker's claim would block for all of it instead of
    # returning None immediately.
    await session.commit()

    asset = claimed
    user_id = str(asset.user_id)
    key_id = str(asset.id)

    try:
        with tempfile.TemporaryDirectory(prefix="zipzop-ingest-") as workspace:
            work = Path(workspace)
            source = work / "source"
            storage.download(asset.storage_key, str(source))

            checksum = _sha256(source)

            probe = ingest.probe(source)

            if probe.duration_ms > settings.max_duration_ms:
                minutes = settings.max_duration_ms // 60_000
                await fail_ingest(
                    session,
                    asset_id,
                    f"This file is longer than the {minutes} minutes we accept.",
                )
                return "failed"

            proxy_key: str | None = None
            thumbnail_key: str | None = None

            if asset.kind is AssetKind.VIDEO:
                if not probe.has_video:
                    await fail_ingest(session, asset_id, "This file has no video track.")
                    return "failed"

                proxy = work / "proxy.mp4"
                ingest.make_proxy(source, proxy)

                thumbnail = work / "thumb.jpg"
                ingest.make_thumbnail(source, thumbnail, probe.duration_ms)

                proxy_key = storage.proxy_key(user_id, key_id)
                thumbnail_key = storage.thumbnail_key(user_id, key_id)
                storage.upload(str(proxy), proxy_key, "video/mp4")
                storage.upload(str(thumbnail), thumbnail_key, "image/jpeg")

            elif asset.kind is AssetKind.AUDIO:
                proxy = work / "proxy.m4a"
                ingest.make_audio_proxy(source, proxy)
                proxy_key = storage.proxy_key(user_id, key_id)
                storage.upload(str(proxy), proxy_key, "audio/mp4")

            document = ingest.make_peaks(source, probe.duration_ms, has_audio=probe.has_audio)
            peaks_key = storage.peaks_key(user_id, key_id)
            storage.put_bytes(peaks_key, _compact_json(document), "application/json")

            await finish_ingest(
                session,
                asset_id,
                duration_ms=probe.duration_ms,
                width=probe.width,
                height=probe.height,
                fps=probe.fps,
                video_codec=probe.video_codec,
                audio_codec=probe.audio_codec,
                audio_channels=probe.audio_channels,
                sample_rate=probe.sample_rate,
                proxy_key=proxy_key,
                thumbnail_key=thumbnail_key,
                peaks_key=peaks_key,
            )
            refreshed = await session.get(MediaAsset, asset_id)
            if refreshed is not None:
                refreshed.checksum_sha256 = checksum
                await session.flush()

            log.info(
                "ingest_complete",
                asset_id=key_id,
                duration_ms=probe.duration_ms,
                kind=asset.kind.value,
            )
            return "ready"

    except ingest.UnreadableMediaError as exc:
        # The message is written for the person who uploaded the file — it goes
        # straight into `failureReason` and onto the screen.
        log.info("ingest_rejected", asset_id=key_id, reason=str(exc))
        await fail_ingest(session, asset_id, str(exc))
        return "failed"
    except TransientFailureError:
        # Already the right shape — re-raised as-is so the task wrapper's
        # `except TransientFailureError` catches it directly. The claim is
        # released first: the retry is a *new* attempt that has to claim again,
        # and one holding a `worker_id` from the attempt that just failed would
        # match nothing and never run. Same reason `analysis_pipeline` calls
        # `requeue` on this path.
        await _release(session, asset_id)
        raise
    except Exception as exc:
        # Not bad media, not one of our own transient markers: an S3 client
        # exception, a dropped database connection, anything unclassified.
        # Wrapped and re-raised rather than swallowed — the asset is left
        # exactly as it was (still `probing`), and the task decides whether to
        # retry or give up. Writing `failed` here, as this used to, told the
        # user their upload was bad when the truth was that our infrastructure
        # blinked.
        log.warning("ingest_transient_failure", asset_id=key_id, error=type(exc).__name__)
        await _release(session, asset_id)
        raise TransientFailureError(f"ingest failed for {key_id}: {exc}") from exc


async def _release(session: AsyncSession, asset_id: uuid.UUID) -> None:
    """Hand the asset back so the retry — ours or the sweep's — can claim it.

    Wrapped, because this runs on the failure path and the failure may well be
    the database itself: a release that raises would replace a
    `TransientFailureError` the task knows how to retry with an exception it
    does not. The asset is then left claimed, which is exactly the case the
    sweep's stuck-`probing` check exists for, so nothing is lost — it is
    recovered five minutes later instead of immediately.
    """
    try:
        await session.rollback()
        await release_ingest_claim(session, asset_id)
        await session.commit()
    except Exception:  # pragma: no cover - the database is already failing
        log.warning("ingest_release_failed", asset_id=str(asset_id))


def _sha256(path: Path) -> str:
    """Streamed, not read into memory — this runs on 2 GB files."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_json(document: object) -> bytes:
    import json

    # No spaces: a 60-minute file is 360 000 numbers, and ", " instead of ","
    # is a third of a megabyte of nothing.
    return json.dumps(document, separators=(",", ":")).encode()
