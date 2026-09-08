"""What a job actually costs, measured — not estimated.

🟠 **The number that decides whether the `beta` plan makes money.**

`SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is flagged in
docs/11-m4-notes.md §8 as *a heuristic, not a measurement*, and every tier's
allowance derives from it. At $19.99 an error there was absorbed. At $3.99 — net
of the 15% commission and processing, about **$3.28** to cover a month of
transcription, trimming, storage and export for a user with 800 credits — it is
not.

This module turns the heuristic into something checkable. It reads what already
happened: `jobs` carries `started_at`, `finished_at`, `tool`,
`credits_reserved`, and since M6 `media_duration_ms`, so the measured
seconds-per-minute is a `GROUP BY` rather than an investigation.

**It reports and does not adjust.** The same rule the ledger reconciliation
follows, for the same reason: a heuristic that silently retunes itself from live
traffic is one nobody can reason about, and a pricing change should be a
decision somebody made.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, JobTool
from app.services import pricing

#: Below this, the median is noise. Two jobs on a developer's laptop are not a
#: fleet measurement, and reporting a ratio from them would be worse than
#: reporting nothing — it would look like evidence.
MIN_SAMPLES = 5

#: How much slower than the estimate before it is worth saying so out loud.
#: 1.25 rather than 1.0 because an estimate that is occasionally beaten is a
#: healthy estimate; one that is consistently a quarter out is a mispriced plan.
DRIFT_FACTOR = 1.25


@dataclass(frozen=True, slots=True)
class ToolCost:
    tool: JobTool
    samples: int
    #: Median wall-clock seconds per minute of media. The median and not the
    #: mean: one job that hit a cold model download would drag a mean up by more
    #: than every other job in the sample combined.
    measured_seconds_per_minute: float
    #: What `pricing.py` currently assumes.
    assumed_seconds_per_minute: float
    #: Credits charged across the sample, and the media they covered.
    credits_charged: int
    media_minutes: float

    @property
    def drift(self) -> float:
        """Measured over assumed. Above 1 means jobs cost more than priced for."""
        if self.assumed_seconds_per_minute <= 0:
            return 0.0
        return self.measured_seconds_per_minute / self.assumed_seconds_per_minute

    @property
    def is_underpriced(self) -> bool:
        return self.samples >= MIN_SAMPLES and self.drift >= DRIFT_FACTOR


async def measure(session: AsyncSession, *, since_days: int = 30) -> list[ToolCost]:
    """Measured cost per tool, over jobs that actually finished.

    Only `succeeded` jobs, and only those with a media duration recorded. A
    failed job's wall clock says how long it took to fail, which is a different
    question, and a cancelled one says nothing at all.
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days)

    seconds = sa.func.extract("epoch", Job.finished_at - Job.started_at)
    minutes = Job.media_duration_ms / 60_000.0

    rows = await session.execute(
        sa.select(
            Job.tool,
            sa.func.count().label("samples"),
            # `percentile_cont` rather than `avg`: one job that paid for a cold
            # model download would drag a mean past every other job combined.
            sa.func.percentile_cont(0.5)
            .within_group((seconds / minutes).asc())
            .label("median_ratio"),
            sa.func.sum(Job.credits_reserved).label("credits"),
            sa.func.sum(minutes).label("minutes"),
        )
        .where(
            Job.status == JobStatus.SUCCEEDED,
            Job.started_at.is_not(None),
            Job.finished_at.is_not(None),
            Job.media_duration_ms.is_not(None),
            Job.media_duration_ms > 0,
            Job.created_at >= cutoff,
        )
        .group_by(Job.tool)
        .order_by(Job.tool)
    )

    out: list[ToolCost] = []
    for tool, samples, median_ratio, credits, minutes_total in rows.all():
        out.append(
            ToolCost(
                tool=tool,
                samples=int(samples or 0),
                measured_seconds_per_minute=float(median_ratio or 0.0),
                assumed_seconds_per_minute=pricing.SECONDS_PER_MINUTE_OF_MEDIA.get(tool, 10.0),
                credits_charged=int(credits or 0),
                media_minutes=float(minutes_total or 0.0),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class PlanMargin:
    """What one month of a plan's full allowance would cost us to serve.

    Deliberately a **worst case**: it assumes every credit is spent, on the most
    expensive mix. Most users spend a fraction of their allowance, so this is
    the number that says whether the plan survives the users who do not.
    """

    plan: str
    monthly_credits: int
    price_minor: int
    currency: str
    #: Compute seconds if the whole allowance went through the costliest tool.
    worst_case_seconds: float


def worst_case_seconds(monthly_credits: int, costs: list[ToolCost]) -> float:
    """Seconds of worker time an allowance could buy, at its most expensive.

    The costliest tool per credit, because that is the mix a heavy user finds
    without trying — captioning long recordings is both the headline feature and
    the slowest thing phase 1 runs.
    """
    measured = [cost for cost in costs if cost.samples >= MIN_SAMPLES]
    if not measured:
        return 0.0

    per_credit: list[float] = []
    for cost in measured:
        credits_per_minute = pricing.COST_PER_MINUTE.get(cost.tool)
        if not credits_per_minute:
            continue
        per_credit.append(cost.measured_seconds_per_minute / credits_per_minute)

    return monthly_credits * max(per_credit) if per_credit else 0.0
