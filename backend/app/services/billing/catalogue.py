"""What the pricing page shows, and what a top-up costs.

Two things that are *presentation* rather than money: the marketing figure
beside a plan, and the label for its queue band. Both are derived from numbers
that already exist, so a repricing moves them without anyone remembering to.
"""

from dataclasses import dataclass
from typing import Final

from app.models import JobTool, Plan
from app.services import pricing

# --------------------------------------------------------------------------
# "About N videos a month"
# --------------------------------------------------------------------------

#: The reference project the marketing figure is quoted against.
#:
#: **Derived from `pricing.py`, never written down as a number.** The figure
#: exists so a pricing page can say something a person understands; the moment
#: it stops tracking what a job actually costs it becomes a promise the product
#: does not keep.
#:
#: 🟠 The two documents that mention it imply different reference projects.
#: Contract §7's illustrative JSON pairs 300 credits with "3 videos", which
#: works out at ~100 credits each; docs/13-mvp-direction.md §3 calls 800 credits
#: "≈30 videos", which is ~27. The reference below — ten minutes through all
#: four phase-1 tools — sits between them at 60, and is the conservative
#: reading: it counts a full pass rather than an export alone, so the number
#: shown is one the product can always beat. **Credits are the unit; this is
#: marketing**, which is the contract's own framing.
REFERENCE_MINUTES: Final = 10
REFERENCE_TOOLS: Final[tuple[JobTool, ...]] = (
    JobTool.CAPTIONS,
    JobTool.SMART_TRIM,
    JobTool.COLOR_ANALYSIS,
    JobTool.EXPORT,
)


def reference_video_credits() -> int:
    """What one reference project costs, at today's prices."""
    duration_ms = REFERENCE_MINUTES * 60_000
    return sum(pricing.cost_credits(tool, duration_ms) for tool in REFERENCE_TOOLS)


def approx_videos_per_month(monthly_credits: int) -> int:
    """Rounded **down**. A pricing page that rounds up is a pricing page that
    over-promises, and the first month is where trust is won or lost."""
    per_video = reference_video_credits()
    if per_video <= 0:  # pragma: no cover - every tool has a positive price
        return 0
    return max(0, monthly_credits // per_video)


# --------------------------------------------------------------------------
# The queue band, as a word
# --------------------------------------------------------------------------

#: Contract §7: *"`queueLabel` is deliberately a word, not a time — priority is
#: relative, and we do not publish an SLA we have not measured."* The keys are
#: Celery's `priority_steps`, so a plan whose band is not one of them shows the
#: safest label rather than crashing a public page.
_QUEUE_LABELS: Final[dict[int, str]] = {
    0: "Standard",
    10: "Fast",
    20: "Priority",
    30: "Highest",
}


def queue_label(queue_priority: int) -> str:
    return _QUEUE_LABELS.get(queue_priority, "Standard")


def price_minor(plan: Plan, currency: str) -> int | None:
    """The plan's price in one currency, or `None` when it has none there.

    `0` and `None` are different answers: free costs zero, and a tier we cannot
    price in this currency should not be shown as free.
    """
    if currency.upper() == "INR":
        return plan.price_inr_paise if plan.price_inr_paise is not None else _free_or_none(plan)
    return plan.price_usd_cents if plan.price_usd_cents is not None else _free_or_none(plan)


def _free_or_none(plan: Plan) -> int | None:
    """A plan with no price in either currency is the free tier."""
    if plan.price_usd_cents is None and plan.price_inr_paise is None:
        return 0
    return None


# --------------------------------------------------------------------------
# Top-up packs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopupPack:
    code: str
    display_name: str
    credits: int
    usd_cents: int
    inr_paise: int

    def price_minor(self, currency: str) -> int:
        return self.inr_paise if currency.upper() == "INR" else self.usd_cents


#: 🟠 **PLACEHOLDER — these prices are not from the documentation.**
#:
#: Contract §7 specifies the endpoint and names `credits_5000` in its example,
#: but no document states what a pack costs. The figures below are derived from
#: the plan prices rather than invented freely — Pro is 2 500 credits for
#: $19.99, so a subscription credit is about $0.008 — and set slightly above
#: that, because a top-up cheaper per credit than a subscription is a reason not
#: to subscribe. Rupee prices keep the ≈50:1 ratio the plan tiers use.
#:
#: They are in one place so replacing them with real figures is an edit. Like
#: the storage quotas they sit beside in spirit, **they must not ship without
#: the project lead's sign-off** — pricing is not a development decision.
TOPUP_PACKS: Final[dict[str, TopupPack]] = {
    "credits_1000": TopupPack(
        code="credits_1000",
        display_name="1,000 credits",
        credits=1_000,
        usd_cents=999,
        inr_paise=49_900,
    ),
    "credits_5000": TopupPack(
        code="credits_5000",
        display_name="5,000 credits",
        credits=5_000,
        usd_cents=4_499,
        inr_paise=224_900,
    ),
    "credits_15000": TopupPack(
        code="credits_15000",
        display_name="15,000 credits",
        credits=15_000,
        usd_cents=11_999,
        inr_paise=599_900,
    ),
}


def pack(code: str) -> TopupPack | None:
    return TOPUP_PACKS.get(code)
