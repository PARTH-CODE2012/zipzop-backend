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
from app.repositories.media import fail_ingest, finish_ingest
from app.services import ingest, storage

log = get_logger(__name__)


async def run_ingest(session: AsyncSession, asset_id: uuid.UUID) -> str:
    """Returns the asset's final status. Never raises for bad media.

    An unreadable upload is a normal outcome that the user has to be told
    about, not an exception for the queue to retry — retrying a corrupt file
    three times produces the same answer three times and delays the message.
    """
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        log.warning("ingest_asset_missing", asset_id=str(asset_id))
        return "missing"

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
    except Exception as exc:  # pragma: no cover - infrastructure failures
        log.exception("ingest_error", asset_id=key_id, error=type(exc).__name__)
        await fail_ingest(
            session, asset_id, "Something went wrong preparing this file. Please try again."
        )
        return "failed"


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
