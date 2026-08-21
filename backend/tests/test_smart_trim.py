"""Smart trim — what it finds, and what it deliberately leaves alone.

The interesting tests are the refusals. A tool that cuts too eagerly is worse
than no tool: it produces an edit the user has to review word by word, which is
exactly the work they were trying to avoid. So the detectors are asserted for
what they *do not* remove — a deliberate repetition, a "like" used as a verb,
a pause short enough to be breath.

Ranges are in **asset time** throughout. Nothing here knows what clip they will
land on, and the conversion is the client's.
"""

import pathlib

import pytest

from app.services import smart_trim
from app.services.smart_trim import (
    THRESHOLDS,
    Removal,
    detect_fillers,
    detect_repeats,
    detect_stutters,
    merge,
)
from app.services.transcription import Transcript, Word

MEDIUM = THRESHOLDS["medium"]


def _w(text: str, start: int, end: int, confidence: float = 0.95) -> Word:
    return Word(text=text, start_ms=start, end_ms=end, confidence=confidence)


# --------------------------------------------------------------------------
# Fillers
# --------------------------------------------------------------------------


def test_an_unmistakable_filler_is_removed() -> None:
    words = [_w("um", 0, 300), _w("hello", 500, 900)]
    found = detect_fillers(words, MEDIUM, "en")
    assert [(r.start_ms, r.end_ms, r.reason) for r in found] == [(0, 300, "filler")]


def test_a_word_that_is_only_sometimes_a_filler_is_left_mid_sentence() -> None:
    """ "It looks like a duck" must keep its "like".

    Cutting a filler that is doing grammatical work damages the sentence, and
    the user has no way to know which of two hundred cuts did it.
    """
    words = [_w("looks", 0, 200), _w("like", 210, 380), _w("a", 390, 450), _w("duck", 460, 800)]
    assert detect_fillers(words, MEDIUM, "en") == []


def test_the_same_word_alone_between_pauses_is_removed() -> None:
    """ "…and, like, that was it" — said in isolation, it is a filler."""
    words = [_w("and", 0, 200), _w("like", 700, 900), _w("that", 1_500, 1_700)]
    found = detect_fillers(words, MEDIUM, "en")
    assert [r.start_ms for r in found] == [700]


def test_a_low_confidence_match_is_left_alone() -> None:
    """The recogniser is not sure it heard "um" at all."""
    words = [_w("um", 0, 300, confidence=0.2)]
    assert detect_fillers(words, MEDIUM, "en") == []


def test_french_fillers_are_found_in_french() -> None:
    words = [_w("euh", 0, 300), _w("bonjour", 500, 900)]
    assert len(detect_fillers(words, MEDIUM, "fr")) == 1


def test_an_unsupported_language_removes_nothing() -> None:
    """Better nothing than cutting English fillers out of Japanese.

    Captions accept English, French and Hindi; anything else is refused before
    it gets here, so this is a guard rather than a path.
    """
    words = [_w("um", 0, 300), _w("like", 500, 900)]
    assert detect_fillers(words, MEDIUM, "ja") == []


def test_hindi_fillers_are_found_in_both_scripts() -> None:
    """Whisper returns Devanagari, but real Indian speech is often Hinglish and
    comes back in Latin script. A list with one script finds half the fillers."""
    devanagari = [_w("मतलब", 0, 300), _w("मैं", 900, 1_200)]
    romanised = [_w("matlab", 0, 300), _w("main", 900, 1_200)]

    assert len(detect_fillers(devanagari, MEDIUM, "hi")) == 1
    assert len(detect_fillers(romanised, MEDIUM, "hi")) == 1


def test_a_hindi_word_that_is_also_vocabulary_is_kept_mid_sentence() -> None:
    """`तो` is a conjunction, `वो` is the pronoun "that". Cutting either
    mid-sentence removes a word carrying meaning."""
    words = [_w("मैं", 0, 200), _w("तो", 210, 350), _w("गया", 360, 600)]
    assert detect_fillers(words, MEDIUM, "hi") == []


def test_every_accepted_language_has_a_filler_list() -> None:
    """A language we say we caption but have no fillers for is a smart trim
    that quietly does half its job."""
    from app.services.languages import SUPPORTED
    from app.services.smart_trim import FILLERS

    assert set(SUPPORTED) <= set(FILLERS)


# --------------------------------------------------------------------------
# Stutters and repeats
# --------------------------------------------------------------------------


def test_a_stutter_removes_the_first_attempt_not_the_second() -> None:
    """Cutting the second would leave the false start and drop the word the
    speaker actually landed on."""
    words = [_w("the", 0, 200), _w("the", 260, 460), _w("cat", 500, 800)]
    found = detect_stutters(words, MEDIUM)
    assert [(r.start_ms, r.end_ms) for r in found] == [(0, 260)]


def test_a_deliberate_repetition_is_kept() -> None:
    """ "Very, very good" is emphasis, not a stumble. The gap is what tells them
    apart."""
    words = [_w("very", 0, 300), _w("very", 900, 1_200), _w("good", 1_300, 1_600)]
    assert detect_stutters(words, MEDIUM) == []


def test_a_restarted_sentence_keeps_the_second_take() -> None:
    words = [
        _w("I", 0, 100),
        _w("went", 110, 300),
        _w("there", 310, 500),
        _w("I", 520, 600),
        _w("went", 610, 800),
        _w("there", 810, 1_000),
    ]
    found = detect_repeats(words, MEDIUM)
    assert [(r.start_ms, r.end_ms, r.reason) for r in found] == [(0, 500, "repeat")]


def test_ordinary_speech_is_not_a_repeat() -> None:
    words = [
        _w(t, i * 200, i * 200 + 180)
        for i, t in enumerate(["the", "cat", "sat", "on", "the", "mat"])
    ]
    assert detect_repeats(words, MEDIUM) == []


# --------------------------------------------------------------------------
# Strength
# --------------------------------------------------------------------------


def test_the_three_strengths_are_ordered() -> None:
    """Light is the most cautious on every axis. A strength that was tighter in
    one dimension and looser in another would be impossible to describe."""
    light, medium, aggressive = (THRESHOLDS[s] for s in ("light", "medium", "aggressive"))
    assert light.min_silence_ms > medium.min_silence_ms > aggressive.min_silence_ms
    assert light.min_confidence > medium.min_confidence > aggressive.min_confidence
    assert light.padding_ms > medium.padding_ms > aggressive.padding_ms
    assert light.min_repeat_words > medium.min_repeat_words >= aggressive.min_repeat_words


# --------------------------------------------------------------------------
# Merging — the contract requires ordered, non-overlapping ranges
# --------------------------------------------------------------------------


def test_overlapping_findings_become_one_range() -> None:
    """A filler inside a detected silence is found twice. The client must never
    have to reason about two cuts that touch."""
    found = merge(
        [
            Removal(1_000, 3_000, "silence", 0.99),
            Removal(2_500, 3_500, "filler", 0.8),
            Removal(9_000, 9_500, "filler", 0.9),
        ],
        duration_ms=12_000,
    )
    assert [(r.start_ms, r.end_ms, r.reason) for r in found] == [
        (1_000, 3_500, "silence"),
        (9_000, 9_500, "filler"),
    ]
    # The merged range keeps the lower confidence of the pair.
    assert found[0].confidence == 0.8


def test_ranges_are_clamped_to_the_media_and_ordered() -> None:
    found = merge(
        [Removal(8_000, 99_000, "silence", 0.99), Removal(100, 500, "filler", 0.9)],
        duration_ms=10_000,
    )
    assert [(r.start_ms, r.end_ms) for r in found] == [(100, 500), (8_000, 10_000)]


def test_an_empty_analysis_is_a_valid_answer() -> None:
    assert merge([], duration_ms=5_000) == []


# --------------------------------------------------------------------------
# The whole tool
# --------------------------------------------------------------------------


def test_it_reports_what_survives(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(smart_trim, "detect_silence", lambda *a, **k: [])
    transcript = Transcript(
        language="en",
        duration_ms=10_000,
        words=[_w("um", 1_000, 1_400), _w("hello", 2_000, 2_600)],
    )

    result = smart_trim.analyse(tmp_path / "x.mp4", transcript, duration_ms=10_000)

    assert result["analyzedDurationMs"] == 10_000
    assert result["keptDurationMs"] == 9_600
    assert result["removals"] == [
        {"startMs": 1_000, "endMs": 1_400, "reason": "filler", "confidence": 0.95}
    ]


def test_no_transcript_still_gives_a_silence_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A transcript that failed must not take the whole tool down: silence
    detection alone is useful, and the credits are already spent."""
    monkeypatch.setattr(
        smart_trim, "detect_silence", lambda *a, **k: [Removal(0, 900, "silence", 0.99)]
    )

    result = smart_trim.analyse(tmp_path / "x.mp4", None, duration_ms=5_000)

    assert result["keptDurationMs"] == 4_100
    assert len(result["removals"]) == 1
