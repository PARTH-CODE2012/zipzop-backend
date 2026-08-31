"""Running an export, from claim to a file the user can download.

The same shape as `analysis_pipeline`, and deliberately so: claim, work,
settle, refund on failure. What differs is what it produces — media rather than
data — and three things that follow from that.

**It fetches originals, never proxies.** The proxy is 480p and exists so the
browser can scrub a 4K file; exporting from it would cap every render at the
preview's resolution, which is the one thing the milestone is defined against.

**Progress comes from FFmpeg, not from a clock.** `-progress` writes
`out_time_ms` to a file as it encodes; the pipeline reads it against the
timeline's own duration. A bar driven by a timer is a bar that lies, and a
render is long enough for the lie to be obvious.

**The output becomes an asset.** A finished export is a `media_assets` row with
`derived_by_job_id` set, so it appears in the media bin, counts against storage
and can be re-edited — the same treatment every other derived file gets
(`docs/03` §6.4).
"""

import asyncio
import contextlib
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.job import ExportInput
from app.api.schemas.project import TimelineDocument
from app.logging import get_logger
from app.models import AssetKind, AssetStatus, Job, JobStatus, MediaAsset, Plan, Project
from app.models.enums import WatermarkMode
from app.repositories import job as job_repo
from app.services import fonts, job_events, luts, storage
from app.services.analysis_pipeline import (
    JobUnavailableError,
    TransientFailureError,
)
from app.services.credits import CreditLedger
from app.services.render_graph import RenderSettings, build_command
from app.services.render_text import write_ass

log = get_logger(__name__)

#: How often the progress file is read while FFmpeg runs. Two seconds is far
#: more often than the bar needs to move and cheap enough not to matter; the
#: cost of it being slower is a bar that jumps.
PROGRESS_POLL_SECONDS = 2.0


#: A ceiling on one render, from the timeline's own length. Generous, for the
#: same reason `jobs.scratch_seconds` is: killing a render that was merely slow
#: costs the user the wait *and* the credits, twice.
def render_timeout_seconds(duration_ms: int) -> int:
    return int(max(300, (duration_ms / 1000) * 20))


class RenderFailedError(Exception):
    """The render itself could not produce a file, and retrying will not help —
    a filtergraph FFmpeg refuses, a source that will not decode. Permanent, so
    the job fails and the credits go back rather than the queue trying twice
    more at the same cost."""


async def run_export(session: AsyncSession, job_id: uuid.UUID, *, worker_id: str) -> str:
    """Do the render. Returns the job's final status."""
    job = await session.get(Job, job_id)
    if job is None:
        raise TransientFailureError(f"job {job_id} is not visible yet")

    claimed = await job_repo.claim(session, job_id, concurrency_limit=1, worker_id=worker_id)
    if claimed is None:
        await session.rollback()
        raise JobUnavailableError("already claimed or no longer runnable")
    await session.commit()
    await _publish(job, JobStatus.RUNNING, 2)

    try:
        return await _work(session, job)
    except RenderFailedError as exc:
        return await _fail(session, job, code="RENDER_FAILED", message=str(exc))
    except TransientFailureError:
        await job_repo.requeue(session, job.id)
        await session.commit()
        raise
    except Exception as exc:  # pragma: no cover - a bug, not a media problem
        log.exception("export_crashed", job_id=str(job.id))
        return await _fail(session, job, code="INTERNAL", message=str(exc)[:500])


async def _work(session: AsyncSession, job: Job) -> str:
    project = await session.get(Project, job.project_id) if job.project_id else None
    if project is None:
        raise RenderFailedError("the project this export belongs to is gone")

    settings, document = await _plan_render(session, job, project)

    with tempfile.TemporaryDirectory(prefix="zipzop-export-") as workspace:
        work = Path(workspace)
        sources = await _fetch_sources(session, job, document, work)
        await _progress(session, job, 15)

        subtitles = write_ass(
            document,
            width=settings.width,
            height=settings.height,
            font_name=fonts.devanagari_family(),
            into=work / "captions.ass",
        )

        output = work / "export.mp4"
        try:
            plan = build_command(
                document,
                sources=sources,
                settings=settings,
                output=output,
                lut_path_for=luts.path_for,
                subtitles=subtitles,
                progress_to=str(work / "progress.txt"),
            )
        except (ValueError, luts.LutMissingError) as exc:
            raise RenderFailedError(str(exc)) from exc

        await _run_ffmpeg(session, job, plan.args, work / "progress.txt", plan.duration_ms)

        if not output.exists() or output.stat().st_size == 0:
            raise RenderFailedError("the render produced no file")

        await _progress(session, job, 92)
        asset = await _store_export(session, job, output, settings)

    await job_repo.succeed(
        session,
        job.id,
        result={
            "durationMs": plan.duration_ms,
            "sizeBytes": asset.size_bytes,
            "width": settings.width,
            "height": settings.height,
            "watermarked": settings.watermark,
        },
        output_asset_id=asset.id,
    )
    await session.commit()
    await _publish(job, JobStatus.SUCCEEDED, 100)
    log.info("export_succeeded", job_id=str(job.id), asset_id=str(asset.id))
    return JobStatus.SUCCEEDED.value


async def _plan_render(
    session: AsyncSession, job: Job, project: Project
) -> tuple[RenderSettings, TimelineDocument]:
    """Turn the stored input and the project into settings and a document.

    The watermark is decided **here, from the plan**, and never read from the
    job's input — contract §6.2 is explicit that the client cannot ask for it to
    be left off, and an input field it could set would be exactly that.
    """
    try:
        export_input = ExportInput.model_validate(job.input)
    except Exception as exc:
        raise RenderFailedError(f"this export's settings are unreadable: {exc}") from exc

    try:
        document = TimelineDocument.model_validate(project.timeline)
    except Exception as exc:
        raise RenderFailedError(f"this project's timeline is unreadable: {exc}") from exc

    watermark = True  # free terms, and the default when there is no subscription
    plan_code = await _plan_code(session, job.user_id)
    if plan_code is not None:
        plan = await session.get(Plan, plan_code)
        if plan is not None:
            watermark = plan.watermark is not WatermarkMode.NONE

    settings = RenderSettings.for_preset(
        aspect_ratio=export_input.preset.aspect_ratio,
        height=export_input.preset.height,
        crf=export_input.preset.crf,
        fps=project.fps or 30,
        watermark=watermark,
    )
    return settings, document


async def _plan_code(session: AsyncSession, user_id: uuid.UUID) -> Any:
    import sqlalchemy as sa

    from app.models import Subscription, SubStatus

    return await session.scalar(
        sa.select(Subscription.plan).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )


async def _fetch_sources(
    session: AsyncSession, job: Job, document: TimelineDocument, work: Path
) -> dict[str, Path]:
    """Download every original the timeline references.

    **Originals, not proxies**, and each one once however many clips use it — a
    timeline that cuts between two angles of the same file would otherwise
    download it per cut.
    """
    import sqlalchemy as sa

    from app.api import ids

    wanted: dict[str, uuid.UUID] = {}
    for track in document.tracks:
        if track.kind == "text":
            continue
        for clip in track.clips:
            with contextlib.suppress(Exception):
                wanted[clip.asset_id] = ids.decode(ids.ASSET, clip.asset_id)

    if not wanted:
        raise RenderFailedError("this timeline references no media")

    rows = await session.execute(
        sa.select(MediaAsset).where(
            MediaAsset.id.in_(list(wanted.values())),
            MediaAsset.user_id == job.user_id,
            MediaAsset.deleted_at.is_(None),
        )
    )
    by_id = {asset.id: asset for asset in rows.scalars().all()}

    sources: dict[str, Path] = {}
    for public_id, asset_uuid in wanted.items():
        asset = by_id.get(asset_uuid)
        if asset is None or asset.status is not AssetStatus.READY:
            raise RenderFailedError(f"{public_id} is not available to render")
        destination = work / f"src_{asset_uuid.hex}{Path(asset.storage_key).suffix or '.mp4'}"
        try:
            storage.download(asset.storage_key, str(destination))
        except Exception as exc:
            # Storage, not media: worth retrying, and the queue will.
            raise TransientFailureError(f"could not fetch {asset.storage_key}") from exc
        sources[public_id] = destination
    return sources


async def _run_ffmpeg(
    session: AsyncSession, job: Job, args: list[str], progress_file: Path, duration_ms: int
) -> None:
    """Run the render, reporting progress from FFmpeg's own output.

    `-progress` appends `out_time_ms=…` as it encodes. Reading that against the
    timeline's duration is the only honest source for the bar: the alternative
    is a timer, and a render's speed depends on the source, the filters and
    what else the worker is doing.
    """
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + render_timeout_seconds(duration_ms)
    last_reported = 15

    try:
        while process.poll() is None:
            await asyncio.sleep(PROGRESS_POLL_SECONDS)
            if time.monotonic() > deadline:
                process.kill()
                raise RenderFailedError("this export took too long and was stopped")
            percent = _read_progress(progress_file, duration_ms)
            if percent is not None and percent > last_reported:
                last_reported = percent
                await _progress(session, job, percent)
    finally:
        with contextlib.suppress(Exception):
            process.wait(timeout=10)

    if process.returncode != 0:
        stderr = (process.stderr.read() if process.stderr else b"").decode(errors="replace")
        log.warning("export_ffmpeg_failed", job_id=str(job.id), stderr=stderr[-2000:])
        raise RenderFailedError("we could not render this timeline")


def _read_progress(progress_file: Path, duration_ms: int) -> int | None:
    """The last `out_time_ms` in the file, as a percentage of the timeline.

    Clamped to 90: the encode is not the whole job — the upload after it is
    real time on a large file, and a bar that sits at 100 while something is
    still happening is the same lie as one driven by a clock.
    """
    if duration_ms <= 0 or not progress_file.exists():
        return None
    try:
        text = progress_file.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - the file is being written
        return None
    marker = "out_time_ms="
    position = text.rfind(marker)
    if position == -1:
        return None
    value = text[position + len(marker) :].split("\n", 1)[0].strip()
    if not value.isdigit():
        return None
    # `out_time_ms` is microseconds despite the name — a long-standing FFmpeg
    # quirk. Treating it as milliseconds makes a 60-second render report 0.1%.
    done_ms = int(value) / 1000
    return max(15, min(90, int(done_ms / duration_ms * 100)))


async def _store_export(
    session: AsyncSession, job: Job, output: Path, settings: RenderSettings
) -> MediaAsset:
    """Upload the file and give it a row, so it behaves like any other asset."""
    key = storage.export_key(str(job.user_id), str(job.id))
    try:
        storage.upload(str(output), key, "video/mp4")
    except Exception as exc:
        raise TransientFailureError("could not store the export") from exc

    asset = MediaAsset(
        user_id=job.user_id,
        kind=AssetKind.VIDEO,
        # `ready` immediately: unlike an upload there is nothing to probe. We
        # made this file and know its shape.
        status=AssetStatus.READY,
        storage_key=key,
        original_filename=f"export-{job.id}.mp4",
        mime_type="video/mp4",
        size_bytes=output.stat().st_size,
        width=settings.width,
        height=settings.height,
        duration_ms=int(job.input.get("analysedDurationMs") or 0) or None,
        derived_by_job_id=job.id,
    )
    session.add(asset)
    await session.flush()
    return asset


async def _fail(session: AsyncSession, job: Job, *, code: str, message: str) -> str:
    """Fail and refund, in one transaction — §5.4."""
    await job_repo.fail(session, job.id, error_code=code, error_message=message)
    ledger = CreditLedger(session)
    locked = await ledger.lock_user(job.user_id)
    if locked is not None:
        await ledger.refund(user=locked, job_id=job.id)
    await session.commit()
    await _publish(job, JobStatus.FAILED, job.progress, error={"code": code, "message": message})
    log.info("export_failed", job_id=str(job.id), code=code)
    return JobStatus.FAILED.value


async def _progress(session: AsyncSession, job: Job, percent: int) -> None:
    await job_repo.set_progress(session, job.id, percent)
    await session.commit()
    await _publish(job, JobStatus.RUNNING, percent)


async def _publish(
    job: Job, status: JobStatus, progress: int, error: dict[str, str] | None = None
) -> None:
    await job_events.publish(
        user_id=job.user_id,
        job_id=job.id,
        tool=job.tool,
        status=status,
        progress=progress,
        clip_id=None,
        error=error,
    )


__all__ = ["RenderFailedError", "render_timeout_seconds", "run_export"]
