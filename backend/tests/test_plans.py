"""The per-plan tables in `app/services/plans.py`, and the fifth plan.

These exist because of a specific, documented failure mode: `PlanCode` is a
Postgres enum, `plans` is a table, and the two dictionaries in `plans.py` are
neither. Adding a tier means touching four places, and the two in `plans.py`
are the only ones that fail *silently at request time* rather than at migration
time — on the job-claim path and the upload path, for exactly the users who
have just paid (docs/13-mvp-direction.md §3.3).

So the first test here is not about `beta`. It is about the sixth plan, and the
seventh.
"""

import re

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, PlanCode
from app.services.plans import (
    CONCURRENCY_LIMITS,
    STORAGE_QUOTA_BYTES,
    concurrency_for,
    storage_quota_for,
)
from app.workers.celery_app import celery_app

# --------------------------------------------------------------------------
# The tables must cover the enum — every time, not just today
# --------------------------------------------------------------------------


@pytest.mark.parametrize("plan", list(PlanCode))
def test_every_plan_has_a_concurrency_limit(plan: PlanCode) -> None:
    assert plan in CONCURRENCY_LIMITS, (
        f"{plan.value!r} is in PlanCode but not in CONCURRENCY_LIMITS — "
        "the job-claim path raises for this plan"
    )
    assert set(concurrency_for(plan)) == {"analysis", "render", "inference"}


@pytest.mark.parametrize("plan", list(PlanCode))
def test_every_plan_has_a_storage_quota(plan: PlanCode) -> None:
    assert plan in STORAGE_QUOTA_BYTES, (
        f"{plan.value!r} is in PlanCode but not in STORAGE_QUOTA_BYTES — "
        "the upload path raises for this plan"
    )
    assert storage_quota_for(plan) > 0


def test_a_plan_missing_from_a_table_says_which_file_to_edit() -> None:
    """The message is the point.

    `KeyError: <PlanCode.BETA>` on an upload tells whoever is paged nothing
    about where the fix goes. This should be unreachable — the two tests above
    are what keep it that way — so its whole value is in being readable on the
    day it is not.
    """
    with pytest.raises(RuntimeError, match=re.escape("app/services/plans.py")):
        # A member that exists in the enum but has been removed from the table,
        # simulated by asking the accessor directly with a mangled table.
        from app.services import plans as plans_module

        original = dict(plans_module.STORAGE_QUOTA_BYTES)
        try:
            plans_module.STORAGE_QUOTA_BYTES.pop(PlanCode.BETA)
            storage_quota_for(PlanCode.BETA)
        finally:
            plans_module.STORAGE_QUOTA_BYTES.clear()
            plans_module.STORAGE_QUOTA_BYTES.update(original)


# --------------------------------------------------------------------------
# `beta` in particular
# --------------------------------------------------------------------------


def test_beta_sits_between_free_and_pro_on_concurrency() -> None:
    """Not a copy of Pro's row. Queue position and parallelism are what Pro
    sells; this tier buys volume, resolution and no watermark."""
    assert concurrency_for(PlanCode.BETA) == {"analysis": 2, "render": 1, "inference": 0}
    assert (
        concurrency_for(PlanCode.FREE)["analysis"]
        < concurrency_for(PlanCode.BETA)["analysis"]
        < concurrency_for(PlanCode.PRO)["analysis"]
    )


async def test_beta_queue_priority_is_a_band_celery_actually_has(db: AsyncSession) -> None:
    """⚠️ The trap this test exists for.

    `apply_async(priority=…)` passes the plan's number through untranslated, and
    Redis has no native priority — Celery emulates it with one sub-queue per
    step. A value that is not one of the configured steps is silently mapped to
    a neighbour: no error, no log, just a plan sitting in a queue band nobody
    chose. `beta` is 0 for this reason and not 5.
    """
    steps = celery_app.conf.broker_transport_options["priority_steps"]
    rows = (await db.execute(sa.select(Plan))).scalars().all()
    assert rows, "the plan catalogue is empty; migration 0002 seeds it"
    for plan in rows:
        assert plan.queue_priority in steps, (
            f"plan {plan.code.value!r} has queue_priority {plan.queue_priority}, "
            f"which is not one of Celery's priority_steps {steps}"
        )


async def test_beta_carries_no_watermark(db: AsyncSession) -> None:
    """The single most important value in the row.

    Removing the watermark is the main reason anyone converts off free. A
    paying user who still sees one feels cheated and churns in the first week,
    and this plan exists to convert strangers arriving from a Discord post.
    """
    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    assert beta.watermark.value == "none"
    assert beta.max_export_height == 1080


async def test_beta_is_public_until_someone_retires_it(db: AsyncSession) -> None:
    """Retiring the plan is one boolean, and this is the column it flips.

    Asserted because `is_public` sat in the schema unread from M2 until M6: a
    default that nothing verifies is a default that drifts.
    """
    beta = await db.get(Plan, PlanCode.BETA)
    assert beta is not None
    assert beta.is_public is True
