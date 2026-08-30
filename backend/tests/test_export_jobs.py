"""Creating an export job — contract §6.2.

Export is the first tool that renders a **project** rather than reading an
asset, and it is the first with two refusals no amount of credit fixes: a
timeline that has moved since the user pressed the button, and a resolution the
plan does not include. Those two are most of this file, because they are what
`quote()` gained a whole second branch for.

`test_jobs.py` covers everything export shares with the analysis tools —
replay, the lock, the ledger — and none of that is repeated here.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.models import AssetKind, AssetStatus, MediaAsset, Project

pytestmark = pytest.mark.anyio

V1 = "/v1"


@pytest.fixture(autouse=True)
def _silence_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.tasks import analysis as analysis_task

    monkeypatch.setattr(analysis_task.run_analysis, "apply_async", lambda *a, **k: None)


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={"email": f"{uuid.uuid4().hex[:12]}@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    return {"Authorization": f"Bearer {body['accessToken']}"}, uuid.UUID(
        body["user"]["id"].removeprefix("usr_")
    )


async def _project_with_timeline(
    db: AsyncSession, user_id: uuid.UUID, *, duration_ms: int = 30_000, version: int = 1
) -> Project:
    """A project that looks rendered-upon: a version, a duration, one asset."""
    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
        storage_key=f"originals/{user_id}/{uuid.uuid4()}/source.mp4",
        original_filename="clip.mp4",
        duration_ms=duration_ms,
    )
    db.add(asset)
    await db.flush()

    project = Project(
        user_id=user_id,
        title="Export me",
        version=version,
        duration_ms=duration_ms,
        timeline={"schemaVersion": 1, "tracks": []},
    )
    db.add(project)
    await db.flush()
    return project


def _body(project: Project, *, version: int | None = None, **preset: Any) -> dict[str, Any]:
    return {
        "tool": "export",
        "projectId": ids.encode(ids.PROJECT, project.id),
        "input": {
            "timelineVersion": project.version if version is None else version,
            "preset": {"resolution": "720p", "aspectRatio": "9:16", **preset},
        },
    }


# --------------------------------------------------------------------------
# The tool is open now
# --------------------------------------------------------------------------


async def test_export_is_accepted_where_it_used_to_be_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`PHASE_1_TOOLS` excluded `export` by design until M5 — a client generated
    from that schema would have offered a button that answered 500."""
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(project))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["tool"] == "export"
    assert body["family"] == "render"
    assert body["creditsReserved"] > 0


async def test_an_export_is_priced_from_the_timeline_and_not_from_an_asset(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Two credits a minute, over the *project's* duration — the one tool with
    no `assetId` to measure."""
    headers, user_id = await _account(client)
    short = await _project_with_timeline(db, user_id, duration_ms=60_000)
    long = await _project_with_timeline(db, user_id, duration_ms=240_000)

    cheap = (await client.post(f"{V1}/jobs", headers=headers, json=_body(short))).json()
    dear = (await client.post(f"{V1}/jobs", headers=headers, json=_body(long))).json()

    assert dear["creditsReserved"] > cheap["creditsReserved"]


async def test_an_empty_timeline_is_refused_before_anything_is_charged(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id, duration_ms=0)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(project))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"


# --------------------------------------------------------------------------
# The stale timeline
# --------------------------------------------------------------------------


async def test_a_stale_timeline_version_is_a_409_and_not_a_render(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The user pressed export on a timeline they were looking at.

    If it has moved since, rendering the current one produces a file of
    something they never approved, and rendering the old one is impossible —
    only the current document is kept. Refusing is the only honest answer
    (contract §6.2).
    """
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id, version=7)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(project, version=6))

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    # Both numbers, so the client can say what happened rather than "try again".
    assert error["details"] == {"sent": 6, "current": 7}


async def test_a_version_from_the_future_is_refused_too(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Not `<`, but `!=`. A client that has somehow got ahead of the server is
    at least as wrong as one behind it, and rendering on its say-so would be
    rendering a document nobody has."""
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id, version=3)

    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(project, version=9))

    assert response.status_code == 409


async def test_nothing_is_reserved_when_the_version_is_stale(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A refusal that charged for itself would be the worst kind."""
    import sqlalchemy as sa

    from app.models import CreditLedgerEntry

    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id, version=2)

    await client.post(f"{V1}/jobs", headers=headers, json=_body(project, version=1))

    rows = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CreditLedgerEntry)
        .where(CreditLedgerEntry.user_id == user_id)
    )
    # Registration grants an allowance; nothing else may have moved.
    assert rows == 1


# --------------------------------------------------------------------------
# The plan ceiling
# --------------------------------------------------------------------------


async def test_a_resolution_above_the_plan_is_refused_with_the_upgrade_named(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A fresh account is on free terms, and free tops out at 720p. `403`, not
    `402`: being told to buy credits for a render the plan forbids at any price
    is the wrong sentence."""
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id)

    response = await client.post(
        f"{V1}/jobs", headers=headers, json=_body(project, resolution="2160p")
    )

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "PLAN_LIMIT_EXCEEDED"
    assert error["details"]["requested"] == 2160
    assert error["details"]["allowed"] == 720
    # Which upgrade, not just that one is needed — contract §6.2's `requiredPlan`.
    assert error["details"]["requiredPlan"] == "business"


async def test_the_ceiling_is_reported_on_the_estimate_too(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`blockedBy` exists so the button can be greyed out with a reason instead
    of failing after the click."""
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id)

    response = await client.post(
        f"{V1}/jobs/estimate", headers=headers, json=_body(project, resolution="1080p")
    )

    assert response.status_code == 200, response.text
    assert response.json()["blockedBy"] == "PLAN_LIMIT_EXCEEDED"


async def test_a_resolution_inside_the_plan_is_allowed(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    project = await _project_with_timeline(db, user_id)

    response = await client.post(
        f"{V1}/jobs", headers=headers, json=_body(project, resolution="720p")
    )

    assert response.status_code == 202, response.text


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


async def test_an_export_without_a_project_is_refused_by_the_schema(
    client: AsyncClient,
) -> None:
    """Caught in the request model so the client gets a 422 naming the field,
    rather than a 404 for a project it never mentioned."""
    headers, _ = await _account(client)

    response = await client.post(
        f"{V1}/jobs",
        headers=headers,
        json={"tool": "export", "input": {"timelineVersion": 1, "preset": {}}},
    )

    assert response.status_code == 422


async def test_somebody_elses_project_is_not_found_rather_than_forbidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Absent, somebody else's and deleted are one answer, the same rule the
    asset path follows — telling them apart leaks which projects exist."""
    _, stranger_id = await _account(client)
    theirs = await _project_with_timeline(db, stranger_id)

    headers, _ = await _account(client)
    response = await client.post(f"{V1}/jobs", headers=headers, json=_body(theirs))

    assert response.status_code == 404


async def test_the_preset_defaults_to_a_vertical_1080p_mp4(client: AsyncClient) -> None:
    """The product is short-form. A client that sends `{}` gets the shape the
    audience actually watches."""
    from app.api.schemas.job import ExportPreset

    preset = ExportPreset()
    assert (preset.resolution, preset.aspect_ratio, preset.format) == ("1080p", "9:16", "mp4")
    assert preset.height == 1080
    assert preset.crf == 18
