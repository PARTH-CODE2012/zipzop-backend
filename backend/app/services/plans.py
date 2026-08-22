"""Plan behaviour that is code rather than data.

Most of a tier is a row in `plans` — credits, prices, export ceiling — so a
repricing is an UPDATE and not a deploy. Two things are not, and both live
here so there is exactly one place to change them.

Concurrency caps (docs/03-backend-architecture.md §5.3) are per *family*, so
they do not fit a single column, and they are an operational safety limit
rather than something marketing changes. Job costs (§5.5) are per *tool*, for
the same reason.
"""

from typing import Final

from app.models import PlanCode

#: Per-user, per-family concurrency. Beyond the limit a job stays `queued` and
#: starts as slots free up — the request still succeeds, so the client never
#: has to handle "try again later" (§5.3).
CONCURRENCY_LIMITS: Final[dict[PlanCode, dict[str, int]]] = {
    PlanCode.FREE: {"analysis": 1, "render": 1, "inference": 0},
    PlanCode.PRO: {"analysis": 3, "render": 2, "inference": 1},
    PlanCode.BUSINESS: {"analysis": 5, "render": 3, "inference": 2},
    PlanCode.STUDIO: {"analysis": 8, "render": 5, "inference": 3},
}


def concurrency_for(plan: PlanCode) -> dict[str, int]:
    return dict(CONCURRENCY_LIMITS[plan])


_GB: Final = 1024**3

#: 🟠 **PLACEHOLDER — these numbers are not from the documentation.**
#:
#: contract §3 rejects an upload for "insufficient storage quota" and §9
#: defines `STORAGE_QUOTA_EXCEEDED` with `usedBytes` and `limitBytes`, but no
#: document states what the limit *is* for any tier. It is the same commercial
#: question as the retention policy that docs/README.md lists as owned by the
#: project lead and still open — storage is the largest recurring cost in the
#: system.
#:
#: The values below exist so the enforcement path is built and tested rather
#: than left as a stub. They are in one dictionary precisely so that replacing
#: them with the real figures — or moving them into a `plans` column — is a
#: small change. **They must not ship as-is.**
STORAGE_QUOTA_BYTES: Final[dict[PlanCode, int]] = {
    PlanCode.FREE: 5 * _GB,
    PlanCode.PRO: 100 * _GB,
    PlanCode.BUSINESS: 500 * _GB,
    PlanCode.STUDIO: 2048 * _GB,
}


def storage_quota_for(plan: PlanCode) -> int:
    return STORAGE_QUOTA_BYTES[plan]
