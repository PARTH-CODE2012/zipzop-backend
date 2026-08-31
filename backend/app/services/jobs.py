"""Quoting a job: what it costs, whether it can run, and where the credits come from.

**One function answers for both endpoints.** Contract §6.1 is explicit that the
estimate is *"exact, not indicative — both endpoints use the same function"*, and
the reason is not tidiness: a price shown on a button that differs from the price
charged on click is the kind of bug users report as theft.

So `quote()` does the whole assessment — resolve the asset, measure the media,
price it, decide which buckets would pay and whether anything blocks it — and
returns a value. `POST /jobs/estimate` serialises it. `POST /jobs` acts on it,
inside a transaction, with the user row locked.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.api.schemas.job import CaptionsInput, ColorAnalysisInput, ExportInput, SmartTrimInput
from app.models import (
    CreditLedgerEntry,
    JobFamily,
    JobTool,
    LedgerReason,
    MediaAsset,
    Plan,
    Subscription,
    SubStatus,
)
from app.models.enums import AssetStatus
from app.repositories.media import MediaAssetRepository
from app.services import pricing
from app.services.credits import Allocation, allocate

AnyJobInput = CaptionsInput | SmartTrimInput | ColorAnalysisInput | ExportInput
#: Everything except `export`, which has no asset and is handled separately.
AssetJobInput = CaptionsInput | SmartTrimInput | ColorAnalysisInput

#: Phase 1's three tools all return data rather than media, so they are all one
#: family. `export` joins `render` in M5; the split already exists in the enum
#: and in the queue routing, so that is a mapping entry and not a change.
FAMILY_FOR_TOOL: dict[JobTool, JobFamily] = {
    JobTool.CAPTIONS: JobFamily.ANALYSIS,
    JobTool.SMART_TRIM: JobFamily.ANALYSIS,
    JobTool.COLOR_ANALYSIS: JobFamily.ANALYSIS,
    JobTool.EXPORT: JobFamily.RENDER,
}


class QuoteRejectionError(Exception):
    """The job cannot be created, and the code says why.

    Carried rather than raised straight away because the estimate has to
    *report* the block without failing: `blockedBy` on a button is the whole
    point of the endpoint.
    """

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(slots=True)
class Quote:
    tool: JobTool
    family: JobFamily
    #: `None` for `export`, which renders a project rather than reading a file.
    asset: MediaAsset | None
    #: What will actually be analysed — the requested range, or the whole file.
    duration_ms: int
    credits: int
    estimated_seconds: int
    priority: int
    allocation: Allocation | None
    rejection: QuoteRejectionError | None

    @property
    def can_run(self) -> bool:
        return self.rejection is None and self.allocation is not None


async def _live_subscription(session: AsyncSession, user_id: uuid.UUID) -> Subscription | None:
    result = await session.execute(
        sa.select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )
    return result.scalar_one_or_none()


async def _spent_this_period(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    """Credits reserved since the period began — what fair use is measured against.

    Reservations, not settlements: a job that is still running has already cost
    us the worker, and a ceiling that only counted finished work could be
    stepped over by starting a thousand jobs at once.
    """
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(-CreditLedgerEntry.delta), 0)).where(
            CreditLedgerEntry.user_id == user_id,
            CreditLedgerEntry.reason == LedgerReason.RESERVE,
            CreditLedgerEntry.created_at >= since,
        )
    )
    return int(total or 0)


async def quote(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_credits: int,
    topup_credits: int,
    tool: JobTool,
    job_input: AnyJobInput,
    project_id: str | None = None,
) -> Quote:
    """Price a job and decide whether it may run. Never writes anything."""
    if isinstance(job_input, ExportInput):
        return await _quote_export(
            session,
            user_id=user_id,
            plan_credits=plan_credits,
            topup_credits=topup_credits,
            job_input=job_input,
            project_id=project_id,
        )

    assets = MediaAssetRepository(session, user_id)

    try:
        asset_uuid = ids.decode(ids.ASSET, job_input.asset_id)
    except Exception:
        raise QuoteRejectionError(
            "NOT_FOUND", "We have no media with that id.", {"assetId": job_input.asset_id}
        ) from None

    asset = await assets.get_visible(asset_uuid)
    if asset is None:
        # Absent covers "no such asset", "somebody else's" and "deleted" at
        # once, and they are deliberately indistinguishable to the caller — the
        # same rule timeline validation follows.
        raise QuoteRejectionError(
            "NOT_FOUND", "We have no media with that id.", {"assetId": job_input.asset_id}
        )
    if asset.status is not AssetStatus.READY:
        raise QuoteRejectionError(
            "UNSUPPORTED_MEDIA",
            "That file is still being prepared.",
            {"assetId": job_input.asset_id, "status": asset.status.value},
        )

    duration_ms = _analysed_duration_ms(asset, job_input)
    if duration_ms <= 0:
        raise QuoteRejectionError(
            "UNSUPPORTED_MEDIA",
            "That file has no duration we can work with.",
            {"assetId": job_input.asset_id},
        )

    credits = pricing.cost_credits(tool, duration_ms)
    seconds = pricing.estimated_seconds(tool, duration_ms)

    subscription = await _live_subscription(session, user_id)
    plan: Plan | None = None
    priority = 0
    if subscription is not None:
        plan = await session.get(Plan, subscription.plan)
        if plan is not None:
            priority = plan.queue_priority

    rejection: QuoteRejectionError | None = None

    # Fair use before affordability: "unlimited" hitting its ceiling is a
    # conversation, not a payment problem, and telling a studio customer they
    # are out of credits would be the wrong sentence entirely (§5.4).
    if plan is not None and plan.fair_use_credits is not None and subscription is not None:
        spent = await _spent_this_period(session, user_id, subscription.current_period_start)
        if spent + credits > plan.fair_use_credits:
            rejection = QuoteRejectionError(
                "FAIR_USE_EXCEEDED",
                "This account has passed its monthly fair-use ceiling. "
                "Get in touch and we will sort it out.",
                {"usedCredits": spent, "limitCredits": plan.fair_use_credits},
            )

    allocation = allocate(credits, plan_credits=plan_credits, topup_credits=topup_credits)
    if rejection is None and allocation is None:
        rejection = QuoteRejectionError(
            "INSUFFICIENT_CREDITS",
            "This job needs more credits than the account holds.",
            {"required": credits, "available": plan_credits + topup_credits},
        )

    return Quote(
        tool=tool,
        family=FAMILY_FOR_TOOL[tool],
        asset=asset,
        duration_ms=duration_ms,
        credits=credits,
        estimated_seconds=seconds,
        priority=priority,
        allocation=allocation,
        rejection=rejection,
    )


async def _quote_export(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_credits: int,
    topup_credits: int,
    job_input: ExportInput,
    project_id: str | None,
) -> Quote:
    """The same assessment, over a project instead of an asset.

    Two checks the analysis tools have no equivalent of, and they run **before**
    anything is priced: rendering a stale timeline and rendering above the
    plan's ceiling are both mistakes no amount of credit fixes.
    """
    from app.models import Project

    if not project_id:
        raise QuoteRejectionError("NOT_FOUND", "An export needs a project.", {})
    try:
        project_uuid = ids.decode(ids.PROJECT, project_id)
    except Exception:
        raise QuoteRejectionError(
            "NOT_FOUND", "We have no project with that id.", {"projectId": project_id}
        ) from None

    project = await session.get(Project, project_uuid)
    # Absent, somebody else's and soft-deleted are deliberately one answer, the
    # same rule the asset path and timeline validation follow.
    if project is None or project.user_id != user_id or project.deleted_at is not None:
        raise QuoteRejectionError(
            "NOT_FOUND", "We have no project with that id.", {"projectId": project_id}
        )

    if project.version != job_input.timeline_version:
        # Not optimistic locking for its own sake. The user pressed export on a
        # timeline they were looking at; if it has moved since, rendering the
        # current one produces a file of something they never approved, and
        # rendering the old one is impossible — we only keep the current
        # document. Refusing is the only honest answer (contract §6.2).
        raise QuoteRejectionError(
            "VERSION_CONFLICT",
            "This project changed since you pressed export. Reload and try again.",
            {"sent": job_input.timeline_version, "current": project.version},
        )

    duration_ms = int(project.duration_ms or 0)
    if duration_ms <= 0:
        raise QuoteRejectionError(
            "UNSUPPORTED_MEDIA",
            "There is nothing on this timeline to export.",
            {"projectId": project_id},
        )

    credits = pricing.cost_credits(JobTool.EXPORT, duration_ms)
    seconds = pricing.estimated_seconds(JobTool.EXPORT, duration_ms)

    subscription = await _live_subscription(session, user_id)
    plan: Plan | None = None
    priority = 0
    if subscription is not None:
        plan = await session.get(Plan, subscription.plan)
        if plan is not None:
            priority = plan.queue_priority

    rejection: QuoteRejectionError | None = None

    # The ceiling, before affordability: being told to buy credits for a render
    # the plan forbids at any price is the wrong sentence.
    #
    # Carried rather than raised so `POST /jobs/estimate` can report it on the
    # button — a 4K option that is greyed out with a reason beats one that
    # fails after the click.
    ceiling = plan.max_export_height if plan is not None else _FREE_EXPORT_HEIGHT
    if job_input.preset.height > ceiling:
        rejection = QuoteRejectionError(
            "PLAN_LIMIT_EXCEEDED",
            f"{job_input.preset.resolution} export is not included in this plan.",
            {
                "requested": job_input.preset.height,
                "allowed": ceiling,
                "requiredPlan": _plan_for_height(job_input.preset.height),
            },
        )

    if rejection is None and plan is not None and plan.fair_use_credits is not None:
        assert subscription is not None
        spent = await _spent_this_period(session, user_id, subscription.current_period_start)
        if spent + credits > plan.fair_use_credits:
            rejection = QuoteRejectionError(
                "FAIR_USE_EXCEEDED",
                "This account has passed its monthly fair-use ceiling. "
                "Get in touch and we will sort it out.",
                {"usedCredits": spent, "limitCredits": plan.fair_use_credits},
            )

    allocation = allocate(credits, plan_credits=plan_credits, topup_credits=topup_credits)
    if rejection is None and allocation is None:
        rejection = QuoteRejectionError(
            "INSUFFICIENT_CREDITS",
            "This job needs more credits than the account holds.",
            {"required": credits, "available": plan_credits + topup_credits},
        )

    return Quote(
        tool=JobTool.EXPORT,
        family=FAMILY_FOR_TOOL[JobTool.EXPORT],
        asset=None,
        duration_ms=duration_ms,
        credits=credits,
        estimated_seconds=seconds,
        priority=priority,
        allocation=allocation,
        rejection=rejection,
    )


#: What an account with no live subscription may export. Matches the seeded
#: `free` plan rather than being a second opinion about it — an account between
#: subscriptions is on free terms, not on none.
_FREE_EXPORT_HEIGHT = 720


def _plan_for_height(height: int) -> str:
    """The cheapest seeded plan whose ceiling covers this height.

    Named in the error so the client can say *which* upgrade, which is what
    contract §6.2's `requiredPlan` is for.
    """
    if height <= 720:
        return "free"
    if height <= 1080:
        return "pro"
    return "business"


def _analysed_duration_ms(asset: MediaAsset, job_input: AssetJobInput) -> int:
    """What the job will actually chew through.

    A range is clamped to the media rather than rejected: a client that asks to
    caption 0-10 minutes of a 6-minute file means "all of it", and charging for
    ten would be charging for silence that does not exist.
    """
    available = int(asset.duration_ms or 0)
    if job_input.range_ms is None:
        return available
    start = max(0, min(job_input.range_ms.start_ms, available))
    end = max(start, min(job_input.range_ms.end_ms, available))
    return end - start


def scratch_seconds(duration_ms: int) -> int:
    """A crude soft timeout for a worker, from the media length.

    Generous on purpose — killing a job that was merely slow costs the user a
    refund and the work, twice.
    """
    return int(max(60, math.ceil(duration_ms / 1000) * 4))
