"""Smart trim — contract §6.2, `smart_trim`.

Finds the parts of a clip worth cutting: silence, filler words, stutters and
repeated phrases. It returns **ranges, not edits**. The client turns them into
splits and removals as one undoable commit, which is what lets someone undo the
whole suggestion with a single press after watching it back.

Every range is in **asset time**, never timeline time. A clip trimmed to start
four seconds in and played at double speed has a different clock from the
timeline it sits on, and the client owns that conversion — putting it here
would mean the result was only valid for the one clip that asked for it, and
wrong for a second clip using the same media.

Silence comes from ffmpeg for free. The other three need a transcript, which is
why this tool is coupled to the engine decision more tightly than the checklist
suggests.
"""

import itertools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.logging import get_logger
from app.services.transcription import Transcript, Word

log = get_logger(__name__)

Strength = Literal["light", "medium", "aggressive"]
Reason = Literal["silence", "filler", "stutter", "repeat"]

FFMPEG_TIMEOUT_SECONDS: Final = 600


@dataclass(frozen=True, slots=True)
class Thresholds:
    """What each strength actually changes.

    Three named settings rather than a slider, because the useful question is
    *"how much do you trust it"* and not *"how many milliseconds of silence".*
    """

    #: Shorter gaps than this are breath and rhythm, not dead air. Cutting them
    #: is what makes an edit sound rushed and unnatural.
    min_silence_ms: int
    #: How quiet counts as silent, in dBFS.
    silence_floor_db: int
    #: Leave this much silence at each end of a cut, so words are not clipped.
    padding_ms: int
    #: Below this confidence a filler match is left alone.
    min_confidence: float
    #: How many words a repeated phrase must have before it is a repeat rather
    #: than ordinary English.
    min_repeat_words: int


THRESHOLDS: Final[dict[Strength, Thresholds]] = {
    # Only obvious dead air and unmistakable fillers.
    "light": Thresholds(
        min_silence_ms=1200,
        silence_floor_db=-40,
        padding_ms=180,
        min_confidence=0.8,
        min_repeat_words=4,
    ),
    "medium": Thresholds(
        min_silence_ms=700,
        silence_floor_db=-35,
        padding_ms=120,
        min_confidence=0.6,
        min_repeat_words=3,
    ),
    # Tight. Expect to reject some of these.
    "aggressive": Thresholds(
        min_silence_ms=400,
        silence_floor_db=-30,
        padding_ms=60,
        min_confidence=0.4,
        min_repeat_words=2,
    ),
}

#: One list per language `app.services.languages` accepts - English, French and
#: Hindi, decided 21 August. A list is per-language by nature: running English's
#: against French speech silently finds nothing, which reads as a tool that does
#: not work rather than one that was never given the words.
FILLERS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        {"um", "uh", "erm", "hmm", "mmm", "like", "so", "basically", "actually", "literally"}
    ),
    "fr": frozenset({"euh", "heu", "bah", "ben", "genre", "voilà", "quoi", "enfin"}),
    # Devanagari **and** romanised, because both reach us. Whisper transcribes
    # Hindi in Devanagari, but a great deal of real Indian speech is Hinglish -
    # Hindi structure with English words in it - and the engine often returns
    # those in Latin script. A list with only one of the two scripts finds half
    # the fillers in the recordings this is actually for.
    "hi": frozenset(
        {
            "मतलब",
            "यानी",
            "वो",
            "अच्छा",
            "हाँ",
            "तो",
            "ऐसे",
            "बस",
            "matlab",
            "yaani",
            "woh",
            "acha",
            "haan",
            "toh",
            "aise",
            "bas",
            # Hinglish speakers use these exactly as English speakers do.
            "um",
            "uh",
            "like",
            "actually",
            "basically",
        }
    ),
}

#: Fillers that are also ordinary words. "So" opens a sentence, "like" compares
#: two things - cutting either mid-sentence damages the meaning, so they are
#: only removed when they stand alone between pauses.
#:
#: Hindi has more of these than English does: `तो`/`toh` is a real conjunction,
#: `वो`/`woh` is the pronoun "that", and `हाँ`/`haan` is the word for yes.
#: Removing any of them mid-sentence would cut a word carrying meaning, so
#: every Hindi entry that is also vocabulary lives here.
AMBIGUOUS_FILLERS: Final[frozenset[str]] = frozenset(
    {
        "like",
        "so",
        "actually",
        "literally",
        "quoi",
        "enfin",
        "voilà",
        "तो",
        "वो",
        "हाँ",
        "अच्छा",
        "बस",
        "toh",
        "woh",
        "haan",
        "acha",
        "bas",
    }
)

#: A gap this long on either side means the word was said in isolation.
ISOLATION_GAP_MS: Final = 220


@dataclass(frozen=True, slots=True)
class Removal:
    start_ms: int
    end_ms: int
    reason: Reason
    confidence: float

    def as_dict(self) -> dict[str, object]:
        return {
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
        }


def analyse(
    source: Path,
    transcript: Transcript | None,
    *,
    strength: Strength = "medium",
    duration_ms: int = 0,
) -> dict[str, object]:
    """Everything worth cutting, ordered and non-overlapping.

    `transcript` may be `None` — silence detection alone is a useful result and
    needs no speech recognition. A transcript that failed should not take the
    whole tool down with it.
    """
    limits = THRESHOLDS[strength]
    total_ms = duration_ms or (transcript.duration_ms if transcript else 0)

    found: list[Removal] = list(detect_silence(source, limits, total_ms))
    if transcript is not None:
        language = transcript.language[:2].lower()
        found += detect_fillers(transcript.words, limits, language)
        found += detect_stutters(transcript.words, limits)
        found += detect_repeats(transcript.words, limits)

    removals = merge(found, total_ms)
    removed_ms = sum(r.end_ms - r.start_ms for r in removals)

    return {
        "analyzedDurationMs": total_ms,
        "keptDurationMs": max(0, total_ms - removed_ms),
        "removals": [r.as_dict() for r in removals],
    }


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


def detect_silence(source: Path, limits: Thresholds, duration_ms: int) -> list[Removal]:
    """ffmpeg's own `silencedetect`. One decode pass, no model.

    The padding is not cosmetic: cutting exactly on the detected boundary
    clips the attack of the next word, which is audible and sounds like a
    fault in the recording rather than an edit.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(source),
            "-af",
            f"silencedetect=noise={limits.silence_floor_db}dB:d={limits.min_silence_ms / 1000}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
        check=False,
    )
    log_text = result.stderr.decode(errors="replace")

    removals: list[Removal] = []
    for match in re.finditer(
        r"silence_start:\s*(-?[\d.]+).*?silence_end:\s*([\d.]+)", log_text, re.S
    ):
        start_ms = int(float(match.group(1)) * 1000) + limits.padding_ms
        end_ms = int(float(match.group(2)) * 1000) - limits.padding_ms
        if end_ms - start_ms < 100:
            continue
        removals.append(
            Removal(
                start_ms=max(0, start_ms),
                end_ms=min(end_ms, duration_ms) if duration_ms else end_ms,
                reason="silence",
                # ffmpeg measured it against a threshold. There is nothing
                # probabilistic about it, and claiming otherwise would make the
                # number meaningless next to the ones that are.
                confidence=0.99,
            )
        )
    return [r for r in removals if r.end_ms > r.start_ms]


def detect_fillers(words: list[Word], limits: Thresholds, language: str) -> list[Removal]:
    vocabulary = FILLERS.get(language)
    if not vocabulary:
        # Better to return nothing than to cut English fillers out of Japanese.
        log.info("no_filler_list", language=language)
        return []

    removals: list[Removal] = []
    for index, word in enumerate(words):
        plain = _plain(word.text)
        if plain not in vocabulary or word.confidence < limits.min_confidence:
            continue
        if plain in AMBIGUOUS_FILLERS and not _stands_alone(words, index):
            continue
        removals.append(
            Removal(
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                reason="filler",
                confidence=word.confidence,
            )
        )
    return removals


def detect_stutters(words: list[Word], limits: Thresholds) -> list[Removal]:
    """The same word twice in a row with almost no gap - "the the", "I-I".

    Only the **first** of the pair is removed. Cutting the second would leave
    the false start and drop the word the speaker actually landed on.
    """
    removals: list[Removal] = []
    for first, second in itertools.pairwise(words):
        if _plain(first.text) != _plain(second.text) or not _plain(first.text):
            continue
        if second.start_ms - first.end_ms > 400:
            continue  # a deliberate repetition for emphasis, not a stumble
        removals.append(
            Removal(
                start_ms=first.start_ms,
                end_ms=second.start_ms,
                reason="stutter",
                confidence=min(first.confidence, second.confidence),
            )
        )
    return removals


def detect_repeats(words: list[Word], limits: Thresholds) -> list[Removal]:
    """A phrase said twice back to back - a restarted sentence.

    Like a stutter, the earlier attempt goes and the later one stays: the
    second take is the one the speaker meant to keep.
    """
    size = max(2, limits.min_repeat_words)
    removals: list[Removal] = []
    index = 0
    while index + size * 2 <= len(words):
        first = [_plain(w.text) for w in words[index : index + size]]
        second = [_plain(w.text) for w in words[index + size : index + size * 2]]
        if first == second and all(first):
            removals.append(
                Removal(
                    start_ms=words[index].start_ms,
                    end_ms=words[index + size - 1].end_ms,
                    reason="repeat",
                    confidence=min(w.confidence for w in words[index : index + size * 2]),
                )
            )
            index += size * 2
        else:
            index += 1
    return removals


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def merge(removals: list[Removal], duration_ms: int) -> list[Removal]:
    """Ordered, non-overlapping, inside the media - as the contract requires.

    Overlaps are guaranteed rather than possible: a filler word sitting inside
    a detected silence is found by both detectors. The overlapping pair is
    merged into one range, keeping the reason of the one that started it, so
    the client never has to reason about two cuts that touch.
    """
    clean = [
        Removal(
            start_ms=max(0, r.start_ms),
            end_ms=min(r.end_ms, duration_ms) if duration_ms else r.end_ms,
            reason=r.reason,
            confidence=r.confidence,
        )
        for r in removals
        if r.end_ms > r.start_ms
    ]
    if not clean:
        return []

    clean.sort(key=lambda r: (r.start_ms, r.end_ms))
    merged = [clean[0]]
    for candidate in clean[1:]:
        last = merged[-1]
        if candidate.start_ms <= last.end_ms:
            merged[-1] = Removal(
                start_ms=last.start_ms,
                end_ms=max(last.end_ms, candidate.end_ms),
                reason=last.reason,
                confidence=min(last.confidence, candidate.confidence),
            )
        else:
            merged.append(candidate)
    return [r for r in merged if r.end_ms > r.start_ms]


def _plain(text: str) -> str:
    return re.sub(r"[^\w']", "", text).strip("'").lower()


def _stands_alone(words: list[Word], index: int) -> bool:
    before = words[index - 1] if index > 0 else None
    after = words[index + 1] if index + 1 < len(words) else None
    gap_before = words[index].start_ms - before.end_ms if before else ISOLATION_GAP_MS
    gap_after = after.start_ms - words[index].end_ms if after else ISOLATION_GAP_MS
    return gap_before >= ISOLATION_GAP_MS or gap_after >= ISOLATION_GAP_MS
