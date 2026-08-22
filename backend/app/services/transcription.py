"""Speech to text, behind one function boundary.

**The boundary is the point.** docs/10-m4-readiness.md §1 asked for the
transcription call to sit behind a single function so that swapping providers
later is a function body rather than an architecture change. Everything above
this module — the captions tool, smart trim, the job pipeline — speaks in
`Word` objects and knows nothing about what produced them.

The engine, decided 20 August: **self-hosted `faster-whisper` on CPU**. Chosen
over a paid API because it has no per-call cost that scales with usage, and its
accuracy at this model size is honestly the same order as a cheap third-party
API. If latency turns out to matter more than cost once this is measured, the
replacement goes inside `_transcribe_with_whisper` and nothing else moves.

⚠️ **The model is downloaded on first use** (~150 MB for `base`) and cached in
`~/.cache/huggingface`. A worker's first captions job therefore takes far longer
than the estimate. Warm the cache when deploying rather than letting the first
customer pay for it.
"""

import math
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)

#: Loaded once per worker process. Loading takes seconds and the object is
#: reusable and thread-safe for sequential calls, so a per-job load would add
#: that to every single job for nothing.
_model: Any = None

TRANSCRIBE_TIMEOUT_SECONDS: Final = 1800

#: The envelope emphasis is measured against. 16 kHz mono is plenty to know how
#: loud a word was, and it decodes an order of magnitude faster than the source.
EMPHASIS_SAMPLE_RATE: Final = 16_000
EMPHASIS_WINDOW_MS: Final = 20


class TranscriptionFailedError(Exception):
    """Permanent. Unreadable audio, no speech, an unsupported language — none of
    them get better by being tried again."""


@dataclass(frozen=True, slots=True)
class Word:
    """One spoken word, in **asset time**.

    Asset time, never timeline time: a clip trimmed to start four seconds in and
    played at double speed has a different clock from the timeline it sits on,
    and the conversion is the client's job (contract §6.2).
    """

    text: str
    start_ms: int
    end_ms: int
    #: 0-1. Below 0.7 is worth flagging in the interface so the user checks it.
    confidence: float
    #: 0-1, from how loud the word was said relative to the rest of the clip.
    emphasis: float = 0.0


@dataclass(frozen=True, slots=True)
class Transcript:
    language: str
    duration_ms: int
    words: list[Word]


def transcribe(
    source: Path,
    *,
    language: str = "auto",
    duration_ms: int = 0,
    on_progress: Any = None,
) -> Transcript:
    """The only entry point. Everything above here is engine-agnostic.

    `on_progress` is called with a 0-1 fraction as segments arrive, because
    transcription is the slowest thing phase 1 runs and a bar that does not move
    for four minutes reads as a hang.
    """
    words = _transcribe_with_whisper(
        source, language=language, duration_ms=duration_ms, on_progress=on_progress
    )
    if not words.words:
        raise TranscriptionFailedError("We could not find any speech in that file.")
    return _with_emphasis(source, words)


def _transcribe_with_whisper(
    source: Path, *, language: str, duration_ms: int, on_progress: Any
) -> Transcript:
    """⚠️ The provider. Replacing the engine means replacing this function."""
    global _model

    try:
        # No type stubs and no py.typed marker; the boundary this module exists
        # to be is exactly where an untyped import belongs.
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - deployment problem
        raise TranscriptionFailedError(
            "This server has no transcription engine installed."
        ) from exc

    if _model is None:
        log.info("whisper_loading", model=settings.whisper_model)
        _model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            # int8 on CPU: roughly twice the speed of float32 for a difference
            # in word error rate that is lost in the noise at this model size.
            compute_type=settings.whisper_compute_type,
        )

    try:
        segments, info = _model.transcribe(
            str(source),
            language=None if language == "auto" else language,
            word_timestamps=True,
            # Whisper hallucinates confident text over silence. The VAD filter
            # is what stops a caption track full of "Thank you." over a pause.
            vad_filter=True,
        )
    except Exception as exc:
        raise TranscriptionFailedError("We could not read the audio in that file.") from exc

    total_ms = duration_ms or int(getattr(info, "duration", 0) * 1000) or 1
    words: list[Word] = []

    # `segments` is a generator: the work happens as it is consumed, which is
    # what makes reporting real progress possible at all.
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            text = str(word.word).strip()
            if not text:
                continue
            words.append(
                Word(
                    text=text,
                    start_ms=max(0, int(word.start * 1000)),
                    end_ms=max(0, int(word.end * 1000)),
                    # faster-whisper reports a probability per word; the
                    # contract's `c` is exactly that.
                    confidence=round(float(getattr(word, "probability", 1.0)), 3),
                )
            )
        if on_progress is not None:
            on_progress(min(1.0, (segment.end * 1000) / total_ms))

    return Transcript(
        language=str(getattr(info, "language", None) or language),
        duration_ms=total_ms,
        words=words,
    )


# --------------------------------------------------------------------------
# Emphasis
# --------------------------------------------------------------------------


def _with_emphasis(source: Path, transcript: Transcript) -> Transcript:
    """How loud each word was, relative to the rest of the clip.

    The contract's `em` drives the caption animation's intensity, and the
    honest signal available for it is loudness: a word said louder than the
    speaker's own baseline is a word they leaned on. It is measured against
    **that speaker in that clip**, not an absolute level, so a quiet recording
    still has emphasis and a loud one is not all emphasis.

    A failure here costs the animation, not the transcript — every word keeps
    its default of zero and the captions still appear.
    """
    try:
        envelope = _rms_envelope(source)
    except Exception as exc:
        log.warning("emphasis_unavailable", error=type(exc).__name__)
        return transcript
    if not envelope:
        return transcript

    loudness = [_window_mean(envelope, w.start_ms, w.end_ms) for w in transcript.words]
    spoken = sorted(v for v in loudness if v > 0)
    if not spoken:
        return transcript

    # Percentiles, not min/max: one clipped syllable or one cough would
    # otherwise define the whole scale and flatten every real word to zero.
    quiet = spoken[int(len(spoken) * 0.2)]
    loud = spoken[int(len(spoken) * 0.9)] if len(spoken) > 1 else spoken[-1]
    span = max(1e-6, loud - quiet)

    return Transcript(
        language=transcript.language,
        duration_ms=transcript.duration_ms,
        words=[
            Word(
                text=word.text,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                confidence=word.confidence,
                emphasis=round(min(1.0, max(0.0, (level - quiet) / span)), 3),
            )
            for word, level in zip(transcript.words, loudness, strict=True)
        ],
    )


def _rms_envelope(source: Path) -> list[float]:
    """One RMS value per 20 ms of mono audio, 0-1.

    RMS here, unlike the waveform's peak: perceived loudness is what emphasis
    means, and a peak is a transient — a plosive on a quiet word would read as
    a shout.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(EMPHASIS_SAMPLE_RATE),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        capture_output=True,
        timeout=TRANSCRIBE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        return []

    raw = result.stdout
    count = len(raw) // 2
    if count == 0:
        return []
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    per_window = EMPHASIS_SAMPLE_RATE * EMPHASIS_WINDOW_MS // 1000

    envelope: list[float] = []
    for start in range(0, count, per_window):
        window = samples[start : start + per_window]
        if not window:
            break
        envelope.append(math.sqrt(sum(s * s for s in window) / len(window)) / 32768)
    return envelope


def _window_mean(envelope: list[float], start_ms: int, end_ms: int) -> float:
    first = max(0, start_ms // EMPHASIS_WINDOW_MS)
    last = min(len(envelope), max(first + 1, end_ms // EMPHASIS_WINDOW_MS))
    window = envelope[first:last]
    return sum(window) / len(window) if window else 0.0
