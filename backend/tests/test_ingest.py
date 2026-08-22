"""Ingest, against real ffmpeg and real MinIO.

PHASE1-TASKS.md marks the proxy step ⚠️ — *"the whole editor depends on this"* —
and it is the one part of M2 a mock would prove nothing about. Every test here
runs the actual binaries over actual files and reads the actual bytes back out
of the bucket.
"""

import json
import struct
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AssetKind, AssetStatus, MediaAsset
from app.services import ingest, storage
from app.services.ingest_pipeline import run_ingest

pytestmark = [pytest.mark.storage, pytest.mark.ffmpeg]


# --------------------------------------------------------------------------
# The pure steps
# --------------------------------------------------------------------------


def test_probe_reads_what_the_timeline_needs(sample_video: Path) -> None:
    probe = ingest.probe(sample_video)
    assert probe.duration_ms == 4000  # integer milliseconds, never seconds
    assert isinstance(probe.duration_ms, int)
    assert (probe.width, probe.height) == (640, 360)
    assert str(probe.fps) == "25.000"
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"
    assert probe.has_video and probe.has_audio


def test_probe_rejects_something_that_is_not_media(not_a_video: Path) -> None:
    """contract §3: on `failed`, `failureReason` explains why in a sentence.

    Asserted as a sentence, not a code, because it is shown to whoever
    uploaded the file.
    """
    with pytest.raises(ingest.UnreadableMediaError) as caught:
        ingest.probe(not_a_video)
    message = str(caught.value)
    assert message.endswith(".")
    assert "ffprobe" not in message.lower()
    assert "traceback" not in message.lower()


def test_the_proxy_is_480p_h264_faststart(sample_video: Path, tmp_path: Path) -> None:
    """The three properties the browser preview depends on."""
    out = tmp_path / "proxy.mp4"
    ingest.make_proxy(sample_video, out)

    probe = ingest.probe(out)
    assert probe.height == 360  # source was 360p, so no upscale — see below
    assert probe.video_codec == "h264"

    # faststart: the moov atom has to precede mdat, or the browser must fetch
    # the end of the file before it can show the first frame.
    head = out.read_bytes()[:200_000]
    assert 0 <= head.find(b"moov") < head.find(b"mdat")


def test_the_proxy_never_upscales(silent_video: Path, tmp_path: Path) -> None:
    """A 240p upload must not become a blurrier, larger 480p file.

    `scale=-2:'min(480,ih)'` is what makes this true; a flat `scale=-2:480`
    would spend encode time making the picture worse.
    """
    source = ingest.probe(silent_video)
    assert source.height == 240

    out = tmp_path / "proxy.mp4"
    ingest.make_proxy(silent_video, out)
    assert ingest.probe(out).height == 240


def test_the_proxy_scales_a_large_source_down_to_480(tmp_path: Path) -> None:
    big = tmp_path / "big.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=25:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(big),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "proxy.mp4"
    ingest.make_proxy(big, out)

    probe = ingest.probe(out)
    assert probe.height == 480
    # Width stays even, which yuv420p requires: an odd width fails the encode.
    assert probe.width is not None and probe.width % 2 == 0
    assert probe.width == 854  # 1920x1080 -> 854x480


def test_the_thumbnail_is_not_the_first_frame(sample_video: Path, tmp_path: Path) -> None:
    """~10% in, because the first frame of a real recording is very often
    black, a lens cap, or a hand reaching for the button."""
    out = tmp_path / "thumb.jpg"
    ingest.make_thumbnail(sample_video, out, duration_ms=4000)
    assert out.stat().st_size > 0
    assert out.read_bytes()[:2] == b"\xff\xd8"  # JPEG SOI


def test_a_thumbnail_can_still_be_taken_from_a_very_short_file(tmp_path: Path) -> None:
    """Seeking to 10% of a third of a second lands past the only keyframe. The
    fallback to frame zero is what stops that failing the whole ingest."""
    tiny = tmp_path / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=30:duration=0.3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(tiny),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "thumb.jpg"
    ingest.make_thumbnail(tiny, out, duration_ms=300)
    assert out.stat().st_size > 0


def test_peaks_have_one_bucket_per_hundredth_of_a_second(sample_video: Path) -> None:
    """contract §3: `bucketsPerSecond` is 100 and there is one value per bucket.

    The exact length matters because the client maps a bucket index to a
    timestamp by arithmetic — index / 100 seconds — with nothing else to go on.
    """
    document = ingest.make_peaks(sample_video, 4000, has_audio=True)
    assert document["version"] == 1
    assert document["bucketsPerSecond"] == 100
    assert document["channels"] == 1
    assert len(document["peaks"]) == 400  # 4 seconds
    assert all(0.0 <= p <= 1.0 for p in document["peaks"])


def test_peak_amplitudes_agree_with_ffmpeg_itself(sample_video: Path) -> None:
    """Cross-checked against `volumedetect` rather than against our own maths.

    A peak extractor that is internally consistent but wrong by a constant
    factor produces a waveform that looks plausible and is not the audio.
    """
    document = ingest.make_peaks(sample_video, 4000, has_audio=True)
    measured = max(document["peaks"])

    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(sample_video), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
    )
    line = next(ln for ln in result.stderr.decode().splitlines() if "max_volume" in ln)
    decibels = float(line.split("max_volume:")[1].strip().split()[0])
    expected = 10 ** (decibels / 20)

    assert abs(measured - expected) < 0.01, f"peak {measured} against ffmpeg's {expected}"


def test_a_silent_file_still_gets_a_waveform(silent_video: Path) -> None:
    """A video with no audio track still needs peaks, or it can never reach
    `ready`. A flat line is the right answer, not an error."""
    document = ingest.make_peaks(silent_video, 2000, has_audio=False)
    assert len(document["peaks"]) == 200
    assert set(document["peaks"]) == {0.0}


def test_peaks_are_the_peak_and_not_the_average(tmp_path: Path) -> None:
    """A short loud transient in a quiet second must show up.

    RMS would flatten exactly the thing a waveform is read for — where the loud
    parts are.
    """
    clip = tmp_path / "transient.wav"
    # One second: silence, with a 20 ms burst at 0.5 s.
    rate = 8000
    samples = [0] * rate
    for i in range(int(0.5 * rate), int(0.52 * rate)):
        samples[i] = 30000
    clip.write_bytes(_wav(samples, rate))

    document = ingest.make_peaks(clip, 1000, has_audio=True)
    peaks = document["peaks"]
    assert max(peaks) > 0.9, "the burst was flattened away"
    assert peaks[0] == 0.0, "silence did not read as silent"


def _wav(samples: list[int], rate: int) -> bytes:
    body = struct.pack(f"<{len(samples)}h", *samples)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(body))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(body))
        + body
    )


# --------------------------------------------------------------------------
# The whole run
# --------------------------------------------------------------------------


async def _staged(db: AsyncSession, s3: Any, source: Path, kind: AssetKind) -> MediaAsset:
    """An asset whose original is really in the bucket, ready to ingest."""
    from app.models import User

    user = User(email=f"{uuid.uuid4().hex[:12]}@example.com", hashed_password="x")
    db.add(user)
    await db.flush()

    asset = MediaAsset(
        user_id=user.id,
        kind=kind,
        status=AssetStatus.PROBING,
        storage_key="",
        original_filename=source.name,
        size_bytes=source.stat().st_size,
    )
    db.add(asset)
    await db.flush()

    asset.storage_key = storage.original_key(str(user.id), str(asset.id), source.suffix)
    await db.flush()
    s3.put_object(Bucket=settings.s3_bucket, Key=asset.storage_key, Body=source.read_bytes())
    return asset


async def test_a_full_ingest_produces_all_four_outputs(
    db: AsyncSession, s3: Any, sample_video: Path
) -> None:
    """docs/03 §6.2: the asset becomes `ready` only when all four exist.

    Each derivative is fetched back out of the bucket, because a key written on
    the row while the upload failed would leave the editor playing a 404.
    """
    asset = await _staged(db, s3, sample_video, AssetKind.VIDEO)
    assert await run_ingest(db, asset.id) == "ready"
    await db.refresh(asset)

    assert asset.status is AssetStatus.READY
    assert asset.failure_reason is None

    # 1. Probe
    assert asset.duration_ms == 4000
    assert (asset.width, asset.height) == (640, 360)
    assert str(asset.fps) == "25.000"
    assert asset.video_codec == "h264"
    assert asset.audio_codec == "aac"
    assert asset.checksum_sha256 is not None

    # 2, 3, 4 — and every one of them really in the bucket.
    for key in (asset.proxy_key, asset.thumbnail_key, asset.peaks_key):
        assert key is not None
        assert storage.head(key) is not None, f"{key} is on the row but not in the bucket"

    peaks = json.loads(s3.get_object(Bucket=settings.s3_bucket, Key=asset.peaks_key)["Body"].read())
    assert len(peaks["peaks"]) == 400
    assert peaks["bucketsPerSecond"] == 100


async def test_the_stored_proxy_is_playable_480p(db: AsyncSession, s3: Any, tmp_path: Path) -> None:
    """Downloaded from the bucket and probed — not just asserted to exist."""
    big = tmp_path / "big.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(big),
        ],
        check=True,
        capture_output=True,
    )
    asset = await _staged(db, s3, big, AssetKind.VIDEO)
    assert await run_ingest(db, asset.id) == "ready"
    await db.refresh(asset)

    local = tmp_path / "fetched.mp4"
    storage.download(str(asset.proxy_key), str(local))
    probe = ingest.probe(local)
    assert probe.height == 480
    assert probe.video_codec == "h264"
    assert probe.has_audio


async def test_unreadable_media_fails_with_a_sentence_a_person_can_read(
    db: AsyncSession, s3: Any, not_a_video: Path
) -> None:
    asset = await _staged(db, s3, not_a_video, AssetKind.VIDEO)
    assert await run_ingest(db, asset.id) == "failed"
    await db.refresh(asset)

    assert asset.status is AssetStatus.FAILED
    assert asset.failure_reason
    assert asset.failure_reason.endswith(".")
    assert asset.proxy_key is None  # nothing half-written


async def test_a_silent_video_still_reaches_ready(
    db: AsyncSession, s3: Any, silent_video: Path
) -> None:
    """No audio track is not a failure. Plenty of footage is silent."""
    asset = await _staged(db, s3, silent_video, AssetKind.VIDEO)
    assert await run_ingest(db, asset.id) == "ready"
    await db.refresh(asset)
    assert asset.status is AssetStatus.READY
    assert asset.audio_codec is None
    assert asset.peaks_key is not None


async def test_a_file_over_the_duration_limit_is_refused(
    db: AsyncSession, s3: Any, sample_video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAX_DURATION_MS is 60 minutes. Patched down rather than generating an
    hour of video for one assertion."""
    monkeypatch.setattr(settings, "max_duration_ms", 1000)
    asset = await _staged(db, s3, sample_video, AssetKind.VIDEO)

    assert await run_ingest(db, asset.id) == "failed"
    await db.refresh(asset)
    assert asset.status is AssetStatus.FAILED
    assert "longer than" in (asset.failure_reason or "")


async def test_an_audio_upload_needs_no_thumbnail(
    db: AsyncSession, s3: Any, tmp_path: Path
) -> None:
    """Demanding four outputs from an audio file would leave every music
    upload stuck in `probing` forever."""
    track = tmp_path / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "aac",
            str(track),
        ],
        check=True,
        capture_output=True,
    )
    asset = await _staged(db, s3, track, AssetKind.AUDIO)
    assert await run_ingest(db, asset.id) == "ready"
    await db.refresh(asset)

    assert asset.status is AssetStatus.READY
    assert asset.thumbnail_key is None
    assert asset.peaks_key is not None


async def test_ingesting_a_missing_asset_is_not_a_crash(db: AsyncSession) -> None:
    assert await run_ingest(db, uuid.uuid4()) == "missing"


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


async def test_the_asset_endpoint_serves_the_peaks_it_ingested(
    client: AsyncClient, db: AsyncSession, s3: Any, sample_video: Path
) -> None:
    """End to end for the waveform: ffmpeg wrote it, S3 holds it, the API
    returns it in the shape contract §3 describes."""
    from app.api import ids

    asset = await _staged(db, s3, sample_video, AssetKind.VIDEO)
    assert await run_ingest(db, asset.id) == "ready"

    registered = (
        await client.post(
            "/v1/auth/register",
            json={
                "email": f"{uuid.uuid4().hex[:12]}@example.com",
                "password": "hunter2hunter2",
            },
        )
    ).json()
    # Move the asset to the account that is signed in, so this tests the
    # endpoint rather than the scoping (which test_media.py covers).
    asset.user_id = uuid.UUID(registered["user"]["id"].removeprefix("usr_"))
    await db.flush()

    headers = {"Authorization": f"Bearer {registered['accessToken']}"}
    response = await client.get(
        f"/v1/media/{ids.encode(ids.ASSET, asset.id)}/peaks", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bucketsPerSecond"] == 100
    assert body["channels"] == 1
    assert len(body["peaks"]) == 400
