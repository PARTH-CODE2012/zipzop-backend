"""Projects and timeline persistence — contract §4 and §5.

The interesting surface here is `PATCH`. It is autosave, so it runs constantly,
from more than one tab, carrying the whole document each time. The tests that
matter are the ones about what happens when it is given something wrong: a
stale version, a timeline that overlaps itself, a clip pointing at somebody
else's footage, a clip that reads past the end of its own.

Assets are inserted directly rather than uploaded. The upload and ingest path
is covered against real MinIO and real ffmpeg in `test_media.py` and
`test_ingest.py`; repeating it here would make every one of these tests pay for
a transcode to prove something about validation.
"""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.models import AssetKind, AssetStatus, MediaAsset, Project, ProjectAsset

V1 = "/v1"


async def _account(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={
                "email": f"{uuid.uuid4().hex[:12]}@example.com",
                "password": "hunter2hunter2",
                "displayName": "Sam",
            },
        )
    ).json()
    headers = {"Authorization": f"Bearer {body['accessToken']}"}
    return headers, uuid.UUID(body["user"]["id"].removeprefix("usr_"))


async def _ready_asset(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    duration_ms: int = 10_000,
    status: AssetStatus = AssetStatus.READY,
) -> str:
    """An asset the timeline is allowed to point at. Returns the public id."""
    asset = MediaAsset(
        user_id=user_id,
        kind=AssetKind.VIDEO,
        status=status,
        storage_key=f"originals/{user_id}/{uuid.uuid4()}/source.mp4",
        proxy_key=f"proxies/{user_id}/{uuid.uuid4()}/proxy.mp4",
        thumbnail_key=f"thumbs/{user_id}/{uuid.uuid4()}/thumb.jpg",
        peaks_key=f"peaks/{user_id}/{uuid.uuid4()}/peaks.json",
        original_filename="clip.mp4",
        mime_type="video/mp4",
        size_bytes=1024,
        duration_ms=duration_ms,
    )
    db.add(asset)
    await db.flush()
    return ids.encode(ids.ASSET, asset.id)


async def _project(client: AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = await client.post(f"{V1}/projects", headers=headers, json=body or {})
    assert response.status_code == 201, response.text
    return dict(response.json())


def _video_track(*clips: dict[str, Any]) -> dict[str, Any]:
    return {"id": "trk_video", "kind": "video", "index": 0, "clips": list(clips)}


def _clip(asset_id: str, **overrides: Any) -> dict[str, Any]:
    clip = {
        "id": f"clp_{uuid.uuid4().hex[:8]}",
        "assetId": asset_id,
        "startMs": 0,
        "durationMs": 4_000,
        "sourceInMs": 0,
        "speed": 1.0,
        "volume": 1.0,
    }
    clip.update(overrides)
    return clip


async def _save(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    tracks: list[dict[str, Any]],
    *,
    version: int = 0,
) -> Any:
    return await client.patch(
        f"{V1}/projects/{project_id}",
        headers=headers,
        json={"timeline": {"schemaVersion": 1, "tracks": tracks}, "version": version},
    )


# --------------------------------------------------------------------------
# Creating and reading
# --------------------------------------------------------------------------


async def test_a_new_project_starts_empty_at_version_zero(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    project = await _project(client, headers, title="Ep. 42 highlights", aspectRatio="9:16")

    assert project["version"] == 0
    assert project["durationMs"] == 0
    assert project["timeline"] == {"schemaVersion": 1, "tracks": []}
    assert project["assets"] == []
    assert project["id"].startswith("prj_")


@pytest.mark.parametrize(
    ("ratio", "width", "height"),
    [("9:16", 1080, 1920), ("16:9", 1920, 1080), ("1:1", 1080, 1080)],
)
async def test_the_canvas_is_derived_from_the_aspect_ratio(
    client: AsyncClient, ratio: str, width: int, height: int
) -> None:
    """contract §5. The client never sends dimensions — if it could, it could
    ask for 4K on a plan that does not include it by writing the numbers."""
    headers, _ = await _account(client)
    project = await _project(client, headers, aspectRatio=ratio)
    assert (project["width"], project["height"]) == (width, height)


async def test_a_canvas_size_sent_by_the_client_is_ignored(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    project = await _project(client, headers, aspectRatio="9:16", width=3840, height=2160)
    assert (project["width"], project["height"]) == (1080, 1920)


async def test_an_unknown_aspect_ratio_is_refused(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    response = await client.post(f"{V1}/projects", headers=headers, json={"aspectRatio": "4:3"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_opening_a_project_returns_its_assets_with_signed_urls(
    client: AsyncClient, db: AsyncSession
) -> None:
    """*"so opening a project is one request rather than one per clip."*"""
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    assert (
        await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])
    ).status_code == 200

    body = (await client.get(f"{V1}/projects/{project['id']}", headers=headers)).json()
    assert [a["id"] for a in body["assets"]] == [asset_id]
    assert body["assets"][0]["proxyUrl"].startswith("http")
    assert "X-Amz-Signature" in body["assets"][0]["proxyUrl"]
    assert body["assets"][0]["durationMs"] == 10_000


async def test_the_list_omits_timelines_and_is_newest_edited_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, _ = await _account(client)
    first = await _project(client, headers, title="First")
    await _project(client, headers, title="Second")

    # The older project is moved to the top by hand rather than by editing it,
    # and the reason is worth knowing before writing another test here:
    # `now()` in Postgres is the *transaction* timestamp, and this whole test
    # runs inside one transaction, so every row written in it carries the same
    # instant and the ordering falls back to the tiebreak. Production writes one
    # transaction per request and never ties. Setting the time explicitly keeps
    # the assertion about the query's ordering instead of about the harness.
    await db.execute(
        sa.update(Project)
        .where(Project.id == uuid.UUID(first["id"].removeprefix("prj_")))
        .values(updated_at=sa.text("now() + interval '1 minute'"))
    )

    body = (await client.get(f"{V1}/projects", headers=headers)).json()
    assert [p["title"] for p in body["items"]] == ["First", "Second"]
    assert "timeline" not in body["items"][0]


async def test_the_list_pages_by_cursor(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    for index in range(5):
        await _project(client, headers, title=f"P{index}")

    first = (await client.get(f"{V1}/projects?limit=2", headers=headers)).json()
    assert len(first["items"]) == 2
    assert first["nextCursor"]

    second = (
        await client.get(f"{V1}/projects?limit=2&cursor={first['nextCursor']}", headers=headers)
    ).json()
    seen = {p["id"] for p in first["items"]} | {p["id"] for p in second["items"]}
    assert len(seen) == 4


# --------------------------------------------------------------------------
# Autosave and versioning
# --------------------------------------------------------------------------


async def test_saving_a_timeline_bumps_the_version_and_derives_the_duration(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [
            _video_track(
                _clip(asset_id, startMs=0, durationMs=4_000),
                _clip(asset_id, startMs=4_000, durationMs=2_500),
            )
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    # Derived from the document, never sent by the client.
    assert body["durationMs"] == 6_500


async def test_a_stale_version_is_a_conflict_that_says_the_current_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The whole point of `409`: the second tab is told what it missed, so it
    can re-fetch without a round trip to find out."""
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    assert (
        await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])
    ).status_code == 200

    stale = await _save(client, headers, project["id"], [_video_track(_clip(asset_id))], version=0)
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    assert error["details"]["currentVersion"] == 1


async def test_two_saves_from_the_same_version_do_not_both_win(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The compare-and-set property, stated as a test.

    Reading the version, comparing it in Python and then writing would let both
    of these through and the second would silently destroy the first.
    """
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    tracks = [_video_track(_clip(asset_id))]
    outcomes = [
        (await _save(client, headers, project["id"], tracks, version=0)).status_code,
        (await _save(client, headers, project["id"], tracks, version=0)).status_code,
    ]
    assert sorted(outcomes) == [200, 409]

    version = await db.scalar(
        sa.select(Project.version).where(
            Project.id == uuid.UUID(project["id"].removeprefix("prj_"))
        )
    )
    assert version == 1


async def test_a_timeline_save_without_a_version_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await client.patch(
        f"{V1}/projects/{project['id']}",
        headers=headers,
        json={"timeline": {"schemaVersion": 1, "tracks": [_video_track(_clip(asset_id))]}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TIMELINE"


async def test_renaming_does_not_bump_the_version(client: AsyncClient) -> None:
    """contract §5. A rename that bumped `version` would turn every other open
    tab's next autosave into a 409 over an edit that cannot conflict."""
    headers, _ = await _account(client)
    project = await _project(client, headers, title="Draft")

    response = await client.patch(
        f"{V1}/projects/{project['id']}", headers=headers, json={"title": "Ep. 42"}
    )
    assert response.status_code == 200
    assert response.json()["version"] == 0

    body = (await client.get(f"{V1}/projects/{project['id']}", headers=headers)).json()
    assert body["title"] == "Ep. 42"
    assert body["version"] == 0


async def test_changing_the_aspect_ratio_moves_the_canvas(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    project = await _project(client, headers, aspectRatio="9:16")

    await client.patch(
        f"{V1}/projects/{project['id']}", headers=headers, json={"aspectRatio": "16:9"}
    )
    body = (await client.get(f"{V1}/projects/{project['id']}", headers=headers)).json()
    assert (body["width"], body["height"]) == (1920, 1080)


# --------------------------------------------------------------------------
# project_assets
# --------------------------------------------------------------------------


async def _asset_rows(db: AsyncSession, project_id: str) -> set[uuid.UUID]:
    rows = await db.execute(
        sa.select(ProjectAsset.asset_id).where(
            ProjectAsset.project_id == uuid.UUID(project_id.removeprefix("prj_"))
        )
    )
    return set(rows.scalars().all())


async def test_project_assets_is_rebuilt_from_the_document_on_every_save(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    first = await _ready_asset(db, user_id)
    second = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    await _save(client, headers, project["id"], [_video_track(_clip(first))], version=0)
    assert await _asset_rows(db, project["id"]) == {uuid.UUID(first.removeprefix("ast_"))}

    # The clip is replaced, so the old reference must go with it — otherwise the
    # RESTRICT keeps holding media the project no longer uses.
    await _save(client, headers, project["id"], [_video_track(_clip(second))], version=1)
    assert await _asset_rows(db, project["id"]) == {uuid.UUID(second.removeprefix("ast_"))}


async def test_a_rejected_save_leaves_project_assets_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    first = await _ready_asset(db, user_id)
    second = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    await _save(client, headers, project["id"], [_video_track(_clip(first))], version=0)
    stale = await _save(client, headers, project["id"], [_video_track(_clip(second))], version=0)

    assert stale.status_code == 409
    assert await _asset_rows(db, project["id"]) == {uuid.UUID(first.removeprefix("ast_"))}


async def test_an_asset_on_a_saved_timeline_cannot_be_deleted(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The `RESTRICT` reaching the user as `ASSET_IN_USE`, through the real save
    path rather than a hand-built row."""
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)
    await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])

    response = await client.delete(f"{V1}/media/{asset_id}", headers=headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ASSET_IN_USE"
    assert project["id"] in error["details"]["projectIds"]


# --------------------------------------------------------------------------
# The eight invariants — contract §4.3
# --------------------------------------------------------------------------


async def test_overlapping_clips_are_refused_and_the_clip_is_named(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    second = _clip(asset_id, startMs=3_000, durationMs=4_000)
    response = await _save(
        client,
        headers,
        project["id"],
        [_video_track(_clip(asset_id, startMs=0, durationMs=4_000), second)],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_TIMELINE"
    assert error["details"]["clipId"] == second["id"]
    assert error["details"]["overlapMs"] == 1_000


async def test_clips_out_of_order_are_refused(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [
            _video_track(
                _clip(asset_id, startMs=5_000, durationMs=1_000),
                _clip(asset_id, startMs=1_000, durationMs=1_000),
            )
        ],
    )
    assert response.status_code == 422
    assert "order" in response.json()["error"]["message"].lower()


async def test_a_zero_length_clip_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client, headers, project["id"], [_video_track(_clip(asset_id, durationMs=0))]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TIMELINE"


async def test_a_duplicate_clip_id_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    """Undo/redo addresses clips by id, so a duplicate makes one unreachable and
    the other undoable twice."""
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [
            _video_track(
                _clip(asset_id, id="clp_same", startMs=0, durationMs=1_000),
                _clip(asset_id, id="clp_same", startMs=2_000, durationMs=1_000),
            )
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["clipId"] == "clp_same"


async def test_a_clip_cannot_read_past_the_end_of_its_media(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id, duration_ms=5_000)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [_video_track(_clip(asset_id, sourceInMs=3_000, durationMs=4_000))],
    )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["needsMs"] == 7_000
    assert details["availableMs"] == 5_000


async def test_speed_is_counted_against_the_source(client: AsyncClient, db: AsyncSession) -> None:
    """At 2x a clip consumes twice its timeline duration from the source. A
    client that forgets produces a timeline that previews fine and runs out of
    frames at export, which is why this is checked here and not there."""
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id, duration_ms=5_000)
    project = await _project(client, headers)

    ok = await _save(
        client, headers, project["id"], [_video_track(_clip(asset_id, durationMs=2_000, speed=2.0))]
    )
    assert ok.status_code == 200

    too_fast = await _save(
        client,
        headers,
        project["id"],
        [_video_track(_clip(asset_id, durationMs=3_000, speed=2.0))],
        version=1,
    )
    assert too_fast.status_code == 422
    assert too_fast.json()["error"]["details"]["needsMs"] == 6_000


async def test_a_clip_pointing_at_an_asset_that_is_not_ready_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id, status=AssetStatus.PROBING)
    project = await _project(client, headers)

    response = await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])
    assert response.status_code == 422
    assert response.json()["error"]["details"]["status"] == "probing"


async def test_a_clip_pointing_at_someone_elses_asset_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Invariant 5 is carried by absence: another account's asset is simply not
    in the scoped lookup, and the caller cannot tell that apart from an id that
    never existed."""
    headers, _ = await _account(client)
    _, stranger_id = await _account(client)
    stranger_asset = await _ready_asset(db, stranger_id)
    project = await _project(client, headers)

    response = await _save(client, headers, project["id"], [_video_track(_clip(stranger_asset))])
    assert response.status_code == 422
    assert response.json()["error"]["details"]["assetId"] == stranger_asset


async def test_a_transition_longer_than_half_its_clip_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [
            _video_track(
                _clip(
                    asset_id,
                    durationMs=1_000,
                    transitionOut={"type": "dissolve", "durationMs": 600},
                ),
                _clip(asset_id, startMs=1_000, durationMs=4_000),
            )
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["maximumMs"] == 500


async def test_phase_one_allows_one_track_of_each_kind(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)

    response = await _save(
        client,
        headers,
        project["id"],
        [
            _video_track(_clip(asset_id)),
            {"id": "trk_video2", "kind": "video", "index": 1, "clips": []},
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["kind"] == "video"


async def test_a_malformed_asset_id_is_an_invalid_timeline_not_a_404(
    client: AsyncClient,
) -> None:
    headers, _ = await _account(client)
    project = await _project(client, headers)

    response = await _save(client, headers, project["id"], [_video_track(_clip("not-an-id"))])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TIMELINE"


# --------------------------------------------------------------------------
# Text track
# --------------------------------------------------------------------------


async def test_a_caption_track_round_trips(client: AsyncClient) -> None:
    """One clip per word is what the Captions tool produces, and the shape has
    to survive a save and a reload unchanged."""
    headers, _ = await _account(client)
    project = await _project(client, headers)

    words = [
        {
            "id": f"clp_cap_{index:03d}",
            "kind": "caption",
            "startMs": index * 300,
            "durationMs": 280,
            "text": word,
            "styleId": "kinetic_bold",
            "position": {"x": 0.5, "y": 0.78, "anchor": "center"},
            "emphasis": 0.42,
        }
        for index, word in enumerate(["we", "just", "arrived", "at", "camp"])
    ]
    response = await _save(
        client, headers, project["id"], [{"id": "trk_text", "kind": "text", "clips": words}]
    )
    assert response.status_code == 200

    body = (await client.get(f"{V1}/projects/{project['id']}", headers=headers)).json()
    stored = body["timeline"]["tracks"][0]["clips"]
    assert [c["text"] for c in stored] == ["we", "just", "arrived", "at", "camp"]
    assert stored[0]["position"] == {"x": 0.5, "y": 0.78, "anchor": "center"}


# --------------------------------------------------------------------------
# Duplicate, delete, scoping
# --------------------------------------------------------------------------


async def test_duplicating_copies_the_document_and_the_asset_references(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers, title="Ep. 42")
    await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])

    response = await client.post(f"{V1}/projects/{project['id']}/duplicate", headers=headers)
    assert response.status_code == 201
    copy = response.json()

    assert copy["id"] != project["id"]
    assert copy["title"] == "Ep. 42 (copy)"
    # A fresh copy has no history anyone can hold a stale version of.
    assert copy["version"] == 0
    assert [a["id"] for a in copy["assets"]] == [asset_id]
    assert copy["timeline"]["tracks"][0]["clips"][0]["assetId"] == asset_id


async def test_deleting_hides_the_project_but_keeps_its_media_held(
    client: AsyncClient, db: AsyncSession
) -> None:
    headers, user_id = await _account(client)
    asset_id = await _ready_asset(db, user_id)
    project = await _project(client, headers)
    await _save(client, headers, project["id"], [_video_track(_clip(asset_id))])

    assert (
        await client.delete(f"{V1}/projects/{project['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get(f"{V1}/projects/{project['id']}", headers=headers)).status_code == 404
    assert project["id"] not in [
        p["id"] for p in (await client.get(f"{V1}/projects", headers=headers)).json()["items"]
    ]

    # Soft delete: the row is still there, and so is its hold on the asset, so a
    # restore inside the retention window finds its footage intact.
    assert await _asset_rows(db, project["id"]) == {uuid.UUID(asset_id.removeprefix("ast_"))}


async def test_no_endpoint_returns_another_accounts_project(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner_headers, owner_id = await _account(client)
    asset_id = await _ready_asset(db, owner_id)
    project = await _project(client, owner_headers)
    await _save(client, owner_headers, project["id"], [_video_track(_clip(asset_id))])

    stranger_headers, _ = await _account(client)
    pid = project["id"]

    assert (await client.get(f"{V1}/projects/{pid}", headers=stranger_headers)).status_code == 404
    assert (
        await client.patch(
            f"{V1}/projects/{pid}", headers=stranger_headers, json={"title": "mine now"}
        )
    ).status_code == 404
    assert (
        await client.post(f"{V1}/projects/{pid}/duplicate", headers=stranger_headers)
    ).status_code == 404
    assert (
        await client.delete(f"{V1}/projects/{pid}", headers=stranger_headers)
    ).status_code == 404
    assert (await client.get(f"{V1}/projects", headers=stranger_headers)).json()["items"] == []


async def test_a_malformed_project_id_is_a_404_not_a_500(client: AsyncClient) -> None:
    headers, _ = await _account(client)
    assert (await client.get(f"{V1}/projects/nonsense", headers=headers)).status_code == 404
    assert (await client.get(f"{V1}/projects/prj_not-a-uuid", headers=headers)).status_code == 404


async def test_project_endpoints_need_an_account(client: AsyncClient) -> None:
    assert (await client.get(f"{V1}/projects")).status_code == 401
    assert (await client.post(f"{V1}/projects", json={})).status_code == 401
    assert (await client.get(f"{V1}/projects/prj_{uuid.uuid4()}")).status_code == 401
