"""Ingest: what ffmpeg does to an upload before the editor can use it.

Four outputs, and the asset is only `ready` when all of them exist
(docs/03-backend-architecture.md §6.2):

| Probe     | duration, dimensions, fps, codecs | prices jobs, lays out the timeline |
| Proxy     | 480p H.264 faststart              | the browser cannot scrub a 4K file |
| Thumbnail | JPEG from ~10% in                 | media bin and project listings     |
| Peaks     | amplitudes at 100 buckets/second  | the timeline waveform              |

**Proxies are not an optimisation, they are the reason browser preview is
possible at all.** Everything in this module is a plain function over local
paths — no database, no S3, no Celery — so each step can be tested against real
media without standing anything up. The task in `app/workers/tasks/ingest.py`
is the part that moves bytes around.
"""

import json
import math
import struct
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from app.logging import get_logger

log = get_logger(__name__)

#: Contract §3: one amplitude per bucket, 100 buckets a second. A 10-minute
#: file is ~60 000 numbers, about 400 KB — fetched once and cached client-side.
BUCKETS_PER_SECOND: Final = 100

#: Peaks are computed from a mono 8 kHz decode. The waveform is a picture a few
#: hundred pixels wide; decoding 48 kHz stereo to draw it would cost twenty
#: times the CPU for a shape nobody can see the difference in.
PEAKS_SAMPLE_RATE: Final = 8_000

PROXY_HEIGHT: Final = 480
THUMBNAIL_HEIGHT: Final = 360
THUMBNAIL_AT_FRACTION: Final = 0.10

FFMPEG_TIMEOUT_SECONDS: Final = 900


class UnreadableMediaError(Exception):
    """The file is not media we can use, with a reason a person can read.

    contract §3: on `failed`, `failureReason` explains why in a sentence. That
    sentence is this exception's message, so it is written for the person who
    uploaded the file rather than for a log.
    """


@dataclass(frozen=True)
class Probe:
    duration_ms: int
    width: int | None
    height: int | None
    fps: Decimal | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: int | None
    sample_rate: int | None
    container: str | None

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def _run(
    args: list[str], *, timeout: int = FFMPEG_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - environment problem
        raise UnreadableMediaError("The server is missing its media tools.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UnreadableMediaError("This file took too long to process.") from exc


# --------------------------------------------------------------------------
# 1. Probe
# --------------------------------------------------------------------------


def probe(path: Path) -> Probe:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise UnreadableMediaError("We could not read this file. It may be corrupt or not a video.")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise UnreadableMediaError("We could not read this file.") from exc

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None and audio is None:
        raise UnreadableMediaError("This file contains no video or audio track.")

    duration_ms = _duration_ms(payload, video, audio)
    if duration_ms <= 0:
        raise UnreadableMediaError("This file has no playable duration.")

    width, height = _display_dimensions(video)

    return Probe(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=_fps(video),
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        container=payload.get("format", {}).get("format_name"),
    )


def _duration_ms(payload: dict[str, Any], *candidates: dict[str, Any] | None) -> int:
    """Milliseconds, integer, rounded once.

    The container's duration is authoritative when present; a stream's is the
    fallback, because some MP4s written by phones leave the format duration
    empty.
    """
    sources: list[Any] = [payload.get("format", {}).get("duration")]
    sources += [c.get("duration") for c in candidates if c]
    for value in sources:
        if value in (None, "", "N/A"):
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return round(seconds * 1000)
    return 0


def _fps(video: dict[str, Any] | None) -> Decimal | None:
    """NUMERIC(7,3), from the rational ffprobe reports.

    `avg_frame_rate` over `r_frame_rate`: the latter is the *base* rate and
    reads 60 on a 29.97 file with duplicated fields.
    """
    if not video:
        return None
    for field in ("avg_frame_rate", "r_frame_rate"):
        raw = video.get(field)
        if not raw or raw == "0/0":
            continue
        try:
            numerator, _, denominator = str(raw).partition("/")
            den = Decimal(denominator or "1")
            if den == 0:
                continue
            return (Decimal(numerator) / den).quantize(Decimal("0.001"))
        except (ValueError, ArithmeticError):
            continue
    return None


def _display_dimensions(video: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Width and height **as displayed**, not as stored.

    A phone recording portrait stores a landscape frame plus a 90° rotation
    flag. ffmpeg applies the rotation on decode, so the proxy comes out
    portrait — and if the stored dimensions were reported as-is, every such
    upload would land on the timeline with its aspect ratio on its side.
    """
    if not video:
        return None, None
    width = video.get("width")
    height = video.get("height")
    if width is None or height is None:
        return None, None

    rotation = _rotation_degrees(video)
    if rotation in (90, 270):
        return int(height), int(width)
    return int(width), int(height)


def _rotation_degrees(video: dict[str, Any]) -> int:
    for entry in video.get("side_data_list", []) or []:
        if "rotation" in entry:
            try:
                return abs(int(entry["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    tags = video.get("tags", {}) or {}
    if "rotate" in tags:
        try:
            return abs(int(tags["rotate"])) % 360
        except (TypeError, ValueError):
            pass
    return 0


# --------------------------------------------------------------------------
# 2. Proxy
# --------------------------------------------------------------------------


def make_proxy(source: Path, destination: Path) -> None:
    """480p H.264, faststart.

    `min(480,ih)` rather than a flat 480: scaling *up* a 240p upload would cost
    encode time and bandwidth to produce a blurrier file than the original.

    `-2` keeps the computed width even, which yuv420p requires — an odd width
    fails the encode outright.

    `+faststart` moves the moov atom to the front. Without it the browser has
    to fetch the end of the file before it can show the first frame, and
    scrubbing a proxy over a slow link becomes unusable.
    """
    result = _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            f"scale=-2:'min({PROXY_HEIGHT},ih)'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    if result.returncode != 0 or not destination.exists():
        log.error("proxy_failed", stderr=result.stderr.decode(errors="replace")[-2000:])
        raise UnreadableMediaError("We could not prepare a preview for this file.")


def make_audio_proxy(source: Path, destination: Path) -> None:
    """An audio-only asset needs a playable derivative too, but not a picture."""
    result = _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    if result.returncode != 0 or not destination.exists():
        raise UnreadableMediaError("We could not prepare a preview for this file.")


# --------------------------------------------------------------------------
# 3. Thumbnail
# --------------------------------------------------------------------------


def make_thumbnail(source: Path, destination: Path, duration_ms: int) -> None:
    """A frame from ~10% in.

    Not frame zero: the first frame of a real recording is very often black,
    a lens cap, or a hand reaching for the button, and a media bin full of
    black rectangles is useless.

    `-ss` before `-i` seeks by keyframe without decoding what comes before,
    which turns a minute of decoding on a long file into a seek.
    """
    at_seconds = max(0.0, (duration_ms / 1000.0) * THUMBNAIL_AT_FRACTION)
    result = _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{at_seconds:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale=-2:'min({THUMBNAIL_HEIGHT},ih)'",
            "-q:v",
            "3",
            str(destination),
        ],
        timeout=120,
    )
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        # A seek past the last keyframe of a very short file yields nothing.
        # Fall back to the first frame rather than failing the whole ingest.
        retry = _run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale=-2:'min({THUMBNAIL_HEIGHT},ih)'",
                "-q:v",
                "3",
                str(destination),
            ],
            timeout=120,
        )
        if retry.returncode != 0 or not destination.exists():
            raise UnreadableMediaError("We could not take a thumbnail from this file.")


# --------------------------------------------------------------------------
# 4. Peaks
# --------------------------------------------------------------------------


def make_peaks(source: Path, duration_ms: int, *, has_audio: bool) -> dict[str, Any]:
    """The waveform, as one amplitude per bucket in 0 to 1.

    Computed here rather than in the browser because the alternative is
    downloading and decoding the whole audio track to draw a few hundred
    pixels (docs/03 §6.2).

    A file with no audio still gets a peaks document — a flat line. The
    timeline draws a track for it either way, and returning nothing would leave
    the asset unable to reach `ready`.
    """
    expected = max(1, math.ceil(duration_ms / 1000 * BUCKETS_PER_SECOND))

    if not has_audio:
        return _peaks_document([0.0] * expected, duration_ms)

    result = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(PEAKS_SAMPLE_RATE),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ]
    )
    if result.returncode != 0:
        log.warning("peaks_decode_failed", stderr=result.stderr.decode(errors="replace")[-500:])
        return _peaks_document([0.0] * expected, duration_ms)

    raw = result.stdout
    sample_count = len(raw) // 2
    samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
    per_bucket = PEAKS_SAMPLE_RATE // BUCKETS_PER_SECOND  # 80

    peaks: list[float] = []
    for start in range(0, sample_count, per_bucket):
        window = samples[start : start + per_bucket]
        if not window:
            break
        # Peak, not RMS: a waveform is read for where the loud parts are, and
        # RMS flattens exactly the transients the eye is looking for.
        peaks.append(round(max(abs(s) for s in window) / 32768, 4))

    # The decode rounds to whole samples, so the last bucket can be partial or
    # missing. Pin the length to the probed duration so the client can map a
    # bucket index to a timestamp by arithmetic alone.
    if len(peaks) > expected:
        del peaks[expected:]
    peaks.extend([0.0] * (expected - len(peaks)))

    return _peaks_document(peaks, duration_ms)


def _peaks_document(peaks: list[float], duration_ms: int) -> dict[str, Any]:
    """Exactly the shape in contract §3."""
    return {
        "version": 1,
        "bucketsPerSecond": BUCKETS_PER_SECOND,
        "channels": 1,
        "durationMs": duration_ms,
        "peaks": peaks,
    }
