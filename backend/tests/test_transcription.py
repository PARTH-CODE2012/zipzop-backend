"""Speech to text, and the emphasis measured alongside it.

The engine is self-hosted `faster-whisper`, decided 20 August. What is asserted
here is the **boundary**, not the model: word-level timings in asset time, a
confidence per word, and an emphasis normalised against the speaker rather than
against an absolute level. Swapping the provider must leave every one of these
assertions passing, which is what makes them worth writing.

The tests that need a real transcript skip when the model is not already
cached — it is ~150 MB, and downloading it on every cold CI runner would turn a
thirty-second suite into a several-minute one.
"""

import pathlib

import pytest

from app.services import transcription
from app.services.transcription import Transcript, Word, _window_mean, _with_emphasis

pytestmark = pytest.mark.anyio


def _word(text: str, start: int, end: int, confidence: float = 0.9) -> Word:
    return Word(text=text, start_ms=start, end_ms=end, confidence=confidence)


# --------------------------------------------------------------------------
# Against the real engine
# --------------------------------------------------------------------------


def test_it_transcribes_real_speech_with_timings(
    spoken_audio: pathlib.Path, whisper_available: bool
) -> None:
    if not whisper_available:
        pytest.skip("transcription model not cached; run a captions job once to warm it")

    transcript = transcription.transcribe(spoken_audio, duration_ms=5_870)

    assert transcript.language == "en"
    assert len(transcript.words) > 8

    # The words the fixture actually says.
    said = " ".join(w.text for w in transcript.words).lower()
    assert "hello" in said
    assert "caption" in said

    for word in transcript.words:
        assert word.end_ms > word.start_ms, f"{word.text} ends before it starts"
        assert 0.0 <= word.confidence <= 1.0
        assert 0.0 <= word.emphasis <= 1.0

    # Ordered, and in asset time - the client converts to timeline time.
    starts = [w.start_ms for w in transcript.words]
    assert starts == sorted(starts)
    assert starts[0] < 2_000  # speech begins near the top of the file


def test_a_file_with_no_speech_is_a_permanent_failure(
    sample_video: pathlib.Path, whisper_available: bool
) -> None:
    """A 440 Hz tone is not speech.

    Permanent, not transient: the same file transcribed three times finds the
    same nothing, and retrying only delays telling the user.
    """
    if not whisper_available:
        pytest.skip("transcription model not cached")

    with pytest.raises(transcription.TranscriptionFailedError):
        transcription.transcribe(sample_video, duration_ms=4_000)


# --------------------------------------------------------------------------
# Emphasis, without the engine
# --------------------------------------------------------------------------


def test_emphasis_is_relative_to_the_speaker_not_an_absolute_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet recording still has emphasis, and a loud one is not all emphasis.

    The scale is built from the clip's own percentiles, so the same sentence
    recorded softly and loudly produces the same shape.
    """
    words = [_word("one", 0, 100), _word("two", 100, 200), _word("three", 200, 300)]

    def envelope_for(level: float) -> list[float]:
        # 20 ms windows: five per word.
        return [level * 0.2] * 5 + [level] * 5 + [level * 0.5] * 5

    for level in (0.05, 0.9):
        monkeypatch.setattr(transcription, "_rms_envelope", lambda _s, lvl=level: envelope_for(lvl))
        result = _with_emphasis(
            pathlib.Path("unused"), Transcript(language="en", duration_ms=300, words=words)
        )
        emphases = [w.emphasis for w in result.words]
        assert emphases[1] > emphases[0], f"the loud word should lead at level {level}"
        assert all(0.0 <= e <= 1.0 for e in emphases)


def test_a_failed_envelope_costs_the_animation_not_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emphasis drives the caption animation. Losing it must not lose the words."""

    def explode(_source: pathlib.Path) -> list[float]:
        raise OSError("ffmpeg is not here")

    monkeypatch.setattr(transcription, "_rms_envelope", explode)
    words = [_word("hello", 0, 200)]

    result = _with_emphasis(
        pathlib.Path("unused"), Transcript(language="en", duration_ms=200, words=words)
    )

    assert [w.text for w in result.words] == ["hello"]
    assert result.words[0].emphasis == 0.0


def test_silence_over_a_word_reads_as_no_emphasis() -> None:
    assert _window_mean([0.0, 0.0, 0.0], 0, 60) == 0.0


def test_a_word_past_the_end_of_the_envelope_does_not_crash() -> None:
    """Whisper's last word can end a few milliseconds past the decoded audio."""
    assert _window_mean([0.5, 0.5], 0, 10_000) == 0.5
