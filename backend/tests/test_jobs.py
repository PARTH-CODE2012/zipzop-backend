"""The job pipeline — contract §6.

`POST /jobs` is where credits move, and the properties worth protecting are the
ones about what happens when it is asked for something it must refuse: a job
larger than the balance, a replayed request, somebody else's footage, a tool
that does not exist yet. The happy path is one test; the rest of this file is
the refusals.

The worker is exercised through `app.repositories.job` directly rather than
through Celery. What is being tested there is the claim — *"if zero rows update,
another worker already has it"* — and a broker in the way would only make that
harder to state.
"""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.models import (
    AssetKind,
    AssetStatus,
    CreditBucket,
    CreditLedgerEntry,
    Job,
    JobStatus,
    LedgerReason,
    MediaAsset,
    User,
)
from app.repositories import job as job_repo

pytestmark = pytest.mark.anyio

V1 = "/v1"


@pytest.fixture(autouse=True)
def _silence_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    """No broker is drained in this suite. The pipeline itself is driven
    directly in `test_analysis.py`; the only thing lost here is the send."""
    from app.workers.tasks import analysis as task_module

    monkeypatch.setattr(task_module.run_analysis, "apply_async", lambda *a, **k: None)


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={
                "email": f"{uuid.uuid4().hex[:12]}@example.com",
                "password": "hunter2hunter2",
                "displayName": "Ada",
            },
        )
    ).json()
    return {"Authorization": f"Bearer {body['accessToken']}"}, uuid.UUID(
        body["user"]["id"].removeprefix("usr_")
    )


async def _ready_asset(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    duration_ms: int = 600_000,
    status: AssetStatus = AssetStatus.READY,
) -> str:
    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=status,
        storage_key=f"originals/{user_id}/{uuid.uuid4()}/source.mp4",
        proxy_key=f"proxies/{user_id}/{uuid.uuid4()}/proxy.mp4",
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=4096,
        duration_ms=duration_ms,
    )
    db.add(asset)
    await db.flush()
    return ids.encode(ids.ASSET, asset.id)


def _body(asset_id: str, **over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool": "color_analysis",
        "input": {"assetId": asset_id, "clipId": "clp_a1"},
    }
    payload.update(over)
    return payload


async def _balance(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, int]:
    user = await db.get(User, user_id)
    assert user is not None
    await db.refresh(user)
    return user.plan_credits, user.topup_credits


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------


async def test_creating_a_job_reserves_credits_and_queues_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=600_000)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued"
    assert job["family"] == "analysis"
    assert job["progress"] == 0
    # 10 minutes of colour analysis at 1 credit a minute.
    assert job["creditsReserved"] == 10
    assert job["reservedFrom"] == {"plan": 10, "topup": 0, "facemapSeconds": 0}
    # Echoed back so the client can route the result to the clip that asked.
    assert job["clipId"] == "clp_a1"
    assert job["estimatedSeconds"] > 0

    plan, _ = await _balance(db, user_id)
    assert plan == 290  # the free plan's 300, less the reservation


async def test_the_reservation_and_the_job_are_one_transaction(
    client: AsyncClient, db: AsyncSession
) -> None:
    """*"A job can never exist without its reservation."*"""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=120_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()

    job_id = uuid.UUID(created["id"].removeprefix("job_"))
    rows = (
        (
            await db.execute(
                sa.select(CreditLedgerEntry).where(
                    CreditLedgerEntry.job_id == job_id,
                    CreditLedgerEntry.reason == LedgerReason.RESERVE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.bucket for r in rows] == [CreditBucket.PLAN]
    assert rows[0].delta == -created["creditsReserved"]


async def test_the_estimate_is_exactly_what_creation_charges(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Contract §6.1: *"exact, not indicative - both endpoints use the same
    function."* A price on a button that differs from the price on click is the
    kind of bug users report as theft."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=623_480)

    quoted = (await client.post(f"{V1}/jobs/estimate", headers=headers, json=_body(asset))).json()
    charged = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()

    assert quoted["credits"] == charged["creditsReserved"]
    assert quoted["wouldReserveFrom"] == charged["reservedFrom"]
    assert quoted["estimatedSeconds"] == charged["estimatedSeconds"]
    assert quoted["sufficientBalance"] is True
    assert quoted["blockedBy"] is None


async def test_a_range_is_priced_on_the_range_not_the_file(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=600_000)

    whole = (await client.post(f"{V1}/jobs/estimate", headers=headers, json=_body(asset))).json()
    part = (
        await client.post(
            f"{V1}/jobs/estimate",
            headers=headers,
            json=_body(asset, input={"assetId": asset, "rangeMs": {"startMs": 0, "endMs": 60_000}}),
        )
    ).json()

    assert whole["credits"] == 10
    assert part["credits"] == 1


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


async def test_a_job_larger_than_the_balance_is_refused_and_creates_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    user = await db.get(User, user_id)
    assert user is not None
    user.plan_credits = 2
    await db.flush()

    asset = await _ready_asset(db, user_id, duration_ms=600_000)
    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "INSUFFICIENT_CREDITS"
    assert response.json()["error"]["details"]["required"] == 10

    count = await db.scalar(sa.select(sa.func.count()).select_from(Job))
    assert count == 0
    plan, _ = await _balance(db, user_id)
    assert plan == 2  # nothing taken


async def test_the_estimate_reports_the_block_instead_of_failing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """*"This lets the client show 'Upgrade' on the button itself instead of
    after a failed click."*"""
    headers, user_id = await _account(client)
    user = await db.get(User, user_id)
    assert user is not None
    user.plan_credits = 2
    await db.flush()
    asset = await _ready_asset(db, user_id, duration_ms=600_000)

    response = await client.post(f"{V1}/jobs/estimate", headers=headers, json=_body(asset))

    assert response.status_code == 200
    body = response.json()
    assert body["credits"] == 10
    assert body["sufficientBalance"] is False
    assert body["blockedBy"] == "INSUFFICIENT_CREDITS"


async def test_an_asset_that_is_not_ready_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, status=AssetStatus.PROBING)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"


async def test_somebody_elses_asset_is_not_found(client: AsyncClient, db: AsyncSession) -> None:
    """Absent, deleted and not-yours are deliberately indistinguishable."""
    _, stranger_id = await _account(client)
    stranger_asset = await _ready_asset(db, stranger_id)
    headers, _ = await _account(client)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(stranger_asset))
    assert response.status_code == 404


async def test_a_tool_that_does_not_ship_yet_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`export` is M5 and the phase-2 tools are phase 2. Accepting them would
    put endpoints in the contract that answer 500."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(asset, tool="export"))
    assert response.status_code == 422


async def test_the_input_is_parsed_for_the_tool_that_was_named(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A `smart_trim` job must keep its `strength`.

    All three input shapes accept `{assetId}`, so a union resolved by trying
    members in order would parse this as captions and drop the field — the job
    would run at the default and nothing would say so.
    """
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)

    created = (
        await client.post(
            f"{V1}/jobs",
            headers=headers,
            json={
                "tool": "smart_trim",
                "input": {"assetId": asset, "strength": "aggressive"},
            },
        )
    ).json()

    job = await db.get(Job, uuid.UUID(created["id"].removeprefix("job_")))
    assert job is not None
    assert job.input["strength"] == "aggressive"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


async def test_a_replayed_key_returns_the_original_and_charges_once(
    client: AsyncClient, db: AsyncSession
) -> None:
    """*"A retry after a network timeout is otherwise indistinguishable from a
    second request, and the user gets charged twice."*"""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=120_000)
    key = {"Idempotency-Key": str(uuid.uuid4())}

    first = await client.post(f"{V1}/jobs", headers={**headers, **key}, json=_body(asset))
    second = await client.post(f"{V1}/jobs", headers={**headers, **key}, json=_body(asset))

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["reservedFrom"] == first.json()["reservedFrom"]

    count = await db.scalar(sa.select(sa.func.count()).select_from(Job))
    assert count == 1
    plan, _ = await _balance(db, user_id)
    assert plan == 298  # charged once


# --------------------------------------------------------------------------
# Reading and cancelling
# --------------------------------------------------------------------------


async def test_a_job_is_readable_and_lists_by_status(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()

    one = await client.get(f"{V1}/jobs/{created['id']}", headers=headers)
    assert one.status_code == 200
    assert one.json()["id"] == created["id"]

    # What a client calls on reconnect to catch up.
    listed = await client.get(f"{V1}/jobs?status=queued", headers=headers)
    assert [j["id"] for j in listed.json()["items"]] == [created["id"]]

    empty = await client.get(f"{V1}/jobs?status=succeeded", headers=headers)
    assert empty.json()["items"] == []


async def test_another_users_job_is_not_readable(client: AsyncClient, db: AsyncSession) -> None:
    owner_headers, owner_id = await _account(client)
    asset = await _ready_asset(db, owner_id, duration_ms=60_000)
    created = (await client.post(f"{V1}/jobs", headers=owner_headers, json=_body(asset))).json()

    stranger_headers, _ = await _account(client)
    response = await client.get(f"{V1}/jobs/{created['id']}", headers=stranger_headers)
    assert response.status_code == 404


async def test_cancelling_refunds_in_full(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=600_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()
    assert (await _balance(db, user_id))[0] == 290

    response = await client.post(f"{V1}/jobs/{created['id']}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert (await _balance(db, user_id))[0] == 300


async def test_cancelling_twice_is_a_conflict(client: AsyncClient, db: AsyncSession) -> None:
    """And the second one must not refund again."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=600_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()

    await client.post(f"{V1}/jobs/{created['id']}/cancel", headers=headers)
    again = await client.post(f"{V1}/jobs/{created['id']}/cancel", headers=headers)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"
    assert (await _balance(db, user_id))[0] == 300  # refunded once, not twice


# --------------------------------------------------------------------------
# The claim
# --------------------------------------------------------------------------


async def test_only_one_worker_can_claim_a_job(client: AsyncClient, db: AsyncSession) -> None:
    """*"If zero rows update, another worker already has it; stop."*"""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()
    job_id = uuid.UUID(created["id"].removeprefix("job_"))

    first = await job_repo.claim(db, job_id, concurrency_limit=5, worker_id="w1")
    second = await job_repo.claim(db, job_id, concurrency_limit=5, worker_id="w2")

    assert first is not None
    assert first.status is JobStatus.RUNNING
    assert first.attempts == 1
    assert second is None


async def test_a_user_at_their_cap_is_not_claimed_and_stays_queued(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Contract §5.3: *"beyond the limit, jobs stay queued and start as slots
    free up"* — never an error the client has to handle."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)
    first = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()
    second = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()

    # free plan: one analysis job at a time.
    claimed = await job_repo.claim(
        db, uuid.UUID(first["id"].removeprefix("job_")), concurrency_limit=1, worker_id="w1"
    )
    blocked = await job_repo.claim(
        db, uuid.UUID(second["id"].removeprefix("job_")), concurrency_limit=1, worker_id="w2"
    )

    assert claimed is not None
    assert blocked is None
    still = await db.get(Job, uuid.UUID(second["id"].removeprefix("job_")))
    assert still is not None
    assert still.status is JobStatus.QUEUED


async def test_progress_never_goes_backwards(client: AsyncClient, db: AsyncSession) -> None:
    """A late checkpoint from a retried attempt must not drag the bar back."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()
    job_id = uuid.UUID(created["id"].removeprefix("job_"))
    await job_repo.claim(db, job_id, concurrency_limit=5, worker_id="w1")

    await job_repo.set_progress(db, job_id, 60)
    await job_repo.set_progress(db, job_id, 20)

    job = await db.get(Job, job_id)
    assert job is not None
    await db.refresh(job)
    assert job.progress == 60


async def test_a_cancelled_job_cannot_be_marked_succeeded(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Its credits have already gone back. Succeeding now would hand over the
    result for free and leave the ledger disagreeing with the row."""
    headers, user_id = await _account(client)
    asset = await _ready_asset(db, user_id, duration_ms=60_000)
    created = (await client.post(f"{V1}/jobs", headers=headers, json=_body(asset))).json()
    job_id = uuid.UUID(created["id"].removeprefix("job_"))

    await client.post(f"{V1}/jobs/{created['id']}/cancel", headers=headers)
    settled = await job_repo.succeed(db, job_id, result={"lut": "cyberpunk"})

    assert settled is False
    job = await db.get(Job, job_id)
    assert job is not None
    await db.refresh(job)
    assert job.status is JobStatus.CANCELLED
