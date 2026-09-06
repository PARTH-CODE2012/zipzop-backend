"""Saved editing settings — the narrow reading of "templates".

Caption style, colour grade, transition defaults, title styling: **the user's
own settings**, saved from one project and reapplied to another
(docs/13-mvp-direction.md §4).

**No worker, no queue, no credits, no new job type.** A template is a subset of
the timeline document, so it sits beside the editing operations rather than
among the AI tools — which keeps true the sentence in
docs/02-scope-v1.md that all three phase-1 tools return analysis, and that is
the sentence explaining why phase 1 needs no GPU.

The server does not interpret `settings`. It stores a JSON fragment whose shape
the editor owns and versions, and enforces the two things that *are* its
business: that a template belongs to one account, and that a name is unique
within it.
"""

import json
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from app.api import ids
from app.api.deps import CurrentUser, Session, general_rate_limit
from app.api.errors import APIError, NotFoundError
from app.api.schemas.common import ApiModel
from app.models import Template

router = APIRouter(prefix="/templates", tags=["templates"])

#: A guard on the size of the JSON blob, not on its shape. A template is a
#: handful of style values; anything approaching this is somebody pasting a
#: whole timeline in, which is not what this is for and not what it can restore.
MAX_SETTINGS_BYTES = 64 * 1024


class TemplateTooLargeError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "TEMPLATE_TOO_LARGE"
    message = "That is more than a set of settings."


class TemplateBody(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    settings: dict[str, Any]


class TemplateResponse(ApiModel):
    id: str
    name: str
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TemplateListResponse(ApiModel):
    items: list[TemplateResponse]


def _out(row: Template) -> TemplateResponse:
    return TemplateResponse(
        id=ids.encode(ids.TEMPLATE, row.id),
        name=row.name,
        settings=row.settings,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "",
    response_model=TemplateListResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Your saved settings",
)
async def list_templates(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TemplateListResponse:
    rows = (
        (
            await session.execute(
                sa.select(Template)
                .where(Template.user_id == user.id)
                .order_by(Template.name)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return TemplateListResponse(items=[_out(row) for row in rows])


@router.put(
    "",
    response_model=TemplateResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="Save the current settings under a name",
)
async def save_template(
    body: TemplateBody, user: CurrentUser, session: Session
) -> TemplateResponse:
    """`PUT`, not `POST`, and that is the semantics rather than a preference.

    Saving under a name that already exists **replaces** it — which is what
    "save my settings as Podcast" means the second time somebody does it. A
    `POST` that created a second row would leave two entries reading identically
    in the list, and the user with no way to tell which is which.
    """
    if len(json.dumps(body.settings)) > MAX_SETTINGS_BYTES:
        # A guard on size, not on shape. A template is a handful of style
        # values; anything near this is a whole timeline pasted in, which this
        # cannot restore and was never meant to hold.
        raise TemplateTooLargeError(
            "A template holds settings, not a whole project.",
            details={"maxBytes": MAX_SETTINGS_BYTES},
        )

    name = body.name.strip()
    existing = await session.scalar(
        sa.select(Template).where(Template.user_id == user.id, Template.name == name)
    )
    if existing is not None:
        existing.settings = body.settings
        await session.flush()
        # **Refreshed, not just flushed.** `updated_at` carries an `onupdate`, so
        # the flush expires it and the attribute is re-read the moment the
        # response is serialised — outside the greenlet asyncpg needs, which is
        # a 500 on the ordinary path of saving over a template you already have.
        await session.refresh(existing)
        return _out(existing)

    row = Template(user_id=user.id, name=name, settings=body.settings)
    session.add(row)
    await session.flush()
    return _out(row)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(general_rate_limit)],
    summary="Delete a saved template",
)
async def delete_template(template_id: str, user: CurrentUser, session: Session) -> None:
    row = await session.get(Template, ids.decode(ids.TEMPLATE, template_id))
    # Absent and somebody else's are one answer, the same rule every other
    # scoped resource here follows.
    if row is None or row.user_id != user.id:
        raise NotFoundError("We have no template with that id.", {"templateId": template_id})
    await session.delete(row)
    await session.flush()
