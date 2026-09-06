"""Print measured cost per job against what the prices assume.

    python -m app.scripts.job_costs [--days 30]

🟠 The report the `beta` plan needs before it can be trusted. Every tier's
allowance derives from `SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py`, which is a
heuristic; this says what the fleet actually did.

Read it the way the ledger reconciliation is read: **it reports and does not
adjust.** A number here that is 1.25 times the assumption is a pricing conversation,
not a patch to `pricing.py` by whoever ran the script.
"""

import argparse
import asyncio
import sys

import sqlalchemy as sa

from app.db import worker_session
from app.models import Plan
from app.services.job_costs import MIN_SAMPLES, measure, worst_case_seconds


async def _report(days: int) -> int:
    async with worker_session() as session:
        costs = await measure(session, since_days=days)

        if not costs:
            print(
                f"No finished jobs with a recorded media duration in the last {days} days.\n"
                "Nothing to measure yet — `media_duration_ms` is written from M6 onward, so\n"
                "jobs that ran before it are deliberately not counted."
            )
            return 0

        print(f"Measured over the last {days} days\n")
        print(f"{'tool':<16}{'n':>5}{'measured':>11}{'assumed':>10}{'drift':>8}   ")
        print("-" * 56)
        underpriced = []
        for cost in costs:
            flag = ""
            if cost.samples < MIN_SAMPLES:
                flag = "  (too few to trust)"
            elif cost.is_underpriced:
                flag = "  UNDERPRICED"
                underpriced.append(cost)
            print(
                f"{cost.tool.value:<16}{cost.samples:>5}"
                f"{cost.measured_seconds_per_minute:>10.1f}s"
                f"{cost.assumed_seconds_per_minute:>9.1f}s"
                f"{cost.drift:>8.2f}{flag}"
            )

        print("\nWorst case per plan — the whole allowance through the costliest tool:\n")
        plans = (
            (await session.execute(sa.select(Plan).order_by(Plan.monthly_credits))).scalars().all()
        )
        for plan in plans:
            seconds = worst_case_seconds(plan.monthly_credits, costs)
            price = plan.price_usd_cents
            money = "free" if not price else f"${price / 100:.2f}"
            if seconds <= 0:
                print(f"  {plan.code.value:<10}{money:>8}   not enough measurements yet")
                continue
            print(
                f"  {plan.code.value:<10}{money:>8}   {seconds / 3600:.1f} worker-hours "
                f"({plan.monthly_credits} credits)"
            )

        if underpriced:
            print(
                "\n🟠 Jobs are costing more than the prices assume for: "
                + ", ".join(cost.tool.value for cost in underpriced)
                + "\nThis is a pricing decision, not a patch to pricing.py."
            )
            return 1
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to measure")
    args = parser.parse_args()
    return asyncio.run(_report(args.days))


if __name__ == "__main__":
    sys.exit(main())
