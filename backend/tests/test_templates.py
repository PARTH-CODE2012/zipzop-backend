"""Saved editing settings — docs/13-mvp-direction.md §4.

Small on purpose. The properties worth defending are the three the server
actually owns: a template belongs to one account, saving under an existing name
replaces it rather than duplicating it, and the blob has a ceiling.

Everything about what a template *contains* is the editor's, and is tested
there — the server stores a JSON fragment whose shape it deliberately does not
interpret.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

V1 = "/v1"

SETTINGS: dict[str, Any] = {
    "version": 1,
    "captionStyle": {
        "color": "#ffffff",
        "fontSize": 48,
        "strokeColor": "#000000",
        "strokeWidth": 3,
    },
    "grade": {"lut": "warm_film", "strength": 0.6},
    "transition": {"type": "dissolve", "durationMs": 400},
}


async def _account(client: AsyncClient) -> dict[str, str]:
    body = (
        await client.post(
            f"{V1}/auth/register",
            json={"email": f"{uuid.uuid4().hex[:12]}@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    return {"Authorization": f"Bearer {body['accessToken']}"}


async def test_settings_are_saved_and_come_back_unchanged(client: AsyncClient) -> None:
    """The server stores the fragment, it does not interpret it.

    That is the whole reason `settings` is JSONB: the document's shape belongs
    to the editor and changes with it, and columns here would mean a migration
    every time a style gained a field the backend never reads.
    """
    headers = await _account(client)

    saved = await client.put(
        f"{V1}/templates", headers=headers, json={"name": "Podcast", "settings": SETTINGS}
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"] == SETTINGS
    assert saved.json()["id"].startswith("tpl_")


async def test_saving_the_same_name_replaces_rather_than_duplicates(
    client: AsyncClient,
) -> None:
    """What "save my settings as Podcast" means the second time.

    A second row would leave two entries reading identically in the list and the
    user no way to tell which is which.
    """
    headers = await _account(client)
    await client.put(
        f"{V1}/templates", headers=headers, json={"name": "Podcast", "settings": SETTINGS}
    )

    changed = {**SETTINGS, "grade": {"lut": "cool_clean", "strength": 0.3}}
    again = await client.put(
        f"{V1}/templates", headers=headers, json={"name": "Podcast", "settings": changed}
    )

    listed = (await client.get(f"{V1}/templates", headers=headers)).json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["settings"]["grade"]["lut"] == "cool_clean"
    assert again.json()["id"] == listed["items"][0]["id"], "the row was replaced, not recreated"


async def test_a_name_is_matched_whatever_the_case(client: AsyncClient) -> None:
    """CITEXT. People name these themselves and then save over them; "Podcast"
    and "podcast" being two templates is a bug report."""
    headers = await _account(client)
    await client.put(
        f"{V1}/templates", headers=headers, json={"name": "Podcast", "settings": SETTINGS}
    )
    await client.put(
        f"{V1}/templates", headers=headers, json={"name": "podcast", "settings": SETTINGS}
    )

    listed = (await client.get(f"{V1}/templates", headers=headers)).json()
    assert len(listed["items"]) == 1


async def test_templates_are_only_ever_your_own(client: AsyncClient) -> None:
    mine = await _account(client)
    theirs = await _account(client)
    await client.put(
        f"{V1}/templates", headers=mine, json={"name": "Podcast", "settings": SETTINGS}
    )

    listed = (await client.get(f"{V1}/templates", headers=theirs)).json()
    assert listed["items"] == []


async def test_deleting_somebody_elses_template_is_a_404(client: AsyncClient) -> None:
    """Absent and somebody else's are one answer, the same rule every other
    scoped resource follows — 403 would confirm the id exists."""
    mine = await _account(client)
    theirs = await _account(client)
    saved = (
        await client.put(
            f"{V1}/templates", headers=mine, json={"name": "Podcast", "settings": SETTINGS}
        )
    ).json()

    response = await client.delete(f"{V1}/templates/{saved['id']}", headers=theirs)
    assert response.status_code == 404

    still_there = (await client.get(f"{V1}/templates", headers=mine)).json()
    assert len(still_there["items"]) == 1


async def test_deleting_your_own_works(client: AsyncClient) -> None:
    headers = await _account(client)
    saved = (
        await client.put(
            f"{V1}/templates", headers=headers, json={"name": "Podcast", "settings": SETTINGS}
        )
    ).json()

    assert (
        await client.delete(f"{V1}/templates/{saved['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get(f"{V1}/templates", headers=headers)).json()["items"] == []


async def test_a_whole_timeline_pasted_in_is_refused(client: AsyncClient) -> None:
    """A guard on size, not shape. Anything near the ceiling is a project, not a
    set of settings — and this cannot restore a project."""
    headers = await _account(client)
    huge = {"clips": [{"id": f"clp_{index}", "text": "x" * 200} for index in range(1_000)]}

    response = await client.put(
        f"{V1}/templates", headers=headers, json={"name": "Too big", "settings": huge}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TEMPLATE_TOO_LARGE"


async def test_templates_need_an_account(client: AsyncClient) -> None:
    assert (await client.get(f"{V1}/templates")).status_code == 401
