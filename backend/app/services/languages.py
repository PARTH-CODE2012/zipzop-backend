"""Which languages Captions accepts, and what a filler sounds like in each.

**The decision this file records was open until 21 August 2026.**
`docs/02-scope-v1.md` §3.4 listed the language list as blocked on the vision
doc's "30+ languages" question, which conflates two different features:
transcribing what was said, and translating it into something else. Phase 1 does
the first and not the second, so the list here is only about *what speech we
accept*, not what we can turn it into.

Answered by the project lead: **English, French and Hindi.** Those are the
markets phase 1 is priced for — `plans` carries INR alongside USD and the
payment provider list has Razorpay in it — and three is a list somebody can
actually check the quality of, which thirty is not.

⚠️ **The engine is not the constraint.** Whisper-family models handle ~99
languages, so adding a fourth is this file plus somebody who speaks it checking
the filler list. What is *not* free is the claim: a language in this list is one
we are saying works, and shipping one nobody has listened to is how "30+
languages" became a marketing number rather than a feature.
"""

from typing import Final

#: BCP-47 primary subtags, which is what the engine reports and what the
#: contract's `language` field carries.
SUPPORTED: Final[dict[str, str]] = {
    "en": "English",
    "fr": "Français",
    "hi": "हिन्दी",
}

#: Detect rather than assume. The contract's own example payload uses it, and it
#: is the right default: a user who has to name their language before they can
#: caption anything has been given homework.
AUTO: Final = "auto"


def is_supported(language: str) -> bool:
    return language == AUTO or language.lower()[:2] in SUPPORTED


def normalise(language: str) -> str:
    """`en-GB` and `EN` are both English. The engine wants the primary subtag."""
    if language == AUTO:
        return AUTO
    return language.lower()[:2]


def display_name(language: str) -> str:
    return SUPPORTED.get(normalise(language), language)
