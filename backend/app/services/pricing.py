"""What a job costs, and roughly how long it will take.

One module, because both numbers are shown to the user *before* they commit and
both must be identical to what `POST /jobs` then charges — contract §6.1:
*"The value is exact, not indicative — both endpoints use the same function."*
Two implementations that agree today are two implementations that disagree the
first time one of them is edited.

Cost is a function of the tool and the media duration only
(docs/03-backend-architecture.md §5.5). It deliberately does not depend on the
plan: a discount belongs in what a plan *grants*, not in what work costs, or a
downgrade mid-project silently reprices everything the user has already been
quoted.
"""

import math
from typing import Final

from app.models import JobTool

#: docs/03-backend-architecture.md §5.5, exactly. Phase-2 tools are here because
#: the table is, and because a missing key would be a `KeyError` at request time
#: rather than a rejection the client can read.
COST_PER_MINUTE: Final[dict[JobTool, int]] = {
    JobTool.CAPTIONS: 2,
    JobTool.SMART_TRIM: 1,
    JobTool.COLOR_ANALYSIS: 1,
    JobTool.EXPORT: 2,
    # phase 2
    JobTool.DENOISE: 3,
    JobTool.DEREVERB: 3,
    JobTool.FACE_MAP: 25,
    JobTool.LIP_SYNC: 20,
}

#: Once the included face-mapping meter is spent (§5.5). Not used in phase 1 —
#: neither tool that draws on it ships until phase 2 — and recorded here so the
#: number lives with the others rather than being reinvented next to them.
FACEMAP_OVERAGE_CREDITS_PER_SECOND: Final = 0.5


def cost_credits(tool: JobTool, duration_ms: int) -> int:
    """Whole minutes, rounded up, minimum one.

    Rounding up is the honest direction: a 61-second clip occupies a worker for
    two minutes' worth of work, and billing it as one would make every short
    clip a small loss. The floor of one minute is what stops a two-second test
    clip being free.
    """
    if tool not in COST_PER_MINUTE:
        raise KeyError(f"no price for {tool}")
    minutes = math.ceil(max(0, duration_ms) / 60_000)
    return COST_PER_MINUTE[tool] * max(1, minutes)


#: 🟠 **Heuristics, not measurements.** Seconds of wall clock per minute of
#: media, on the CPU worker fleet. They exist so the interface can say "about a
#: minute" instead of nothing at all, and they are wrong in the way every
#: unmeasured estimate is wrong.
#:
#: The same caveat the architecture doc attaches to its own credit numbers
#: applies: recalibrate from real jobs once M4 has run some. They are in one
#: dictionary so that is an edit, not an investigation. Nothing in the product
#: may present these as an SLA (§5.3, *"priority is relative, not a promise"*).
SECONDS_PER_MINUTE_OF_MEDIA: Final[dict[JobTool, float]] = {
    # Sampled frames through ffmpeg, not a full decode.
    JobTool.COLOR_ANALYSIS: 3.0,
    # One decode pass for silence detection, plus the transcript it leans on.
    JobTool.SMART_TRIM: 12.0,
    # Whisper-family on CPU is the slowest thing phase 1 runs.
    JobTool.CAPTIONS: 20.0,
    # Encoding is the wall: roughly real time at 1080p on a CPU worker.
    JobTool.EXPORT: 45.0,
}

#: Every job has a floor — process start, the download from S3, the upload back.
BASE_SECONDS: Final = 4


def estimated_seconds(tool: JobTool, duration_ms: int) -> int:
    """A number to put next to a progress bar, never a promise."""
    minutes = max(0.0, duration_ms) / 60_000
    per_minute = SECONDS_PER_MINUTE_OF_MEDIA.get(tool, 10.0)
    return max(1, round(BASE_SECONDS + minutes * per_minute))
