"""Projects and timeline persistence — contract §5.

This is the milestone that makes editing survive a reload, and almost all of
the difficulty is in one endpoint. `PATCH` is autosave: it fires every two
seconds, from as many tabs as the user has open, carrying the whole timeline
each time. Three things follow, and they shape the whole module.

1. **Validation runs on every write.** The document is JSONB and Postgres will
   store any shape at all, so `app/services/timeline.py` is the only guarantee
   the export renderer will get something it can draw.
2. **The version check is a compare-and-set**, not a read-then-write. Two tabs
   that both read version 12 must not both write 13.
3. **`project_assets` is rebuilt from the document**, because that side table
   is what stops an asset being deleted out from under a project.

Everything else here is ordinary CRUD.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api import ids
from app.api.deps import CurrentUser, Session, general_rate_limit
from app.api.errors import InvalidTimelineError, NotFoundError, VersionConflictError
from app.api.schemas.common import Page
from app.api.schemas.project import (
    ASPECT_RATIOS,
    CreateProjectRequest,
    ProjectAssetRef,
    ProjectResponse,
    ProjectSaveResponse,
    ProjectSummary,
    TimelineDocument,
    UpdateProjectRequest,
    empty_timeline,
)
from app.logging import get_logger
from app.models import MediaAsset, Project
from app.repositories.media import MediaAssetRepository
from app.repositories.project import ProjectRepository
from app.services import storage
from app.services import timeline as timeline_service

log = get_logger(__name__)

router = APIRouter(
    prefix="/projects", tags=["projects"], dependencies=[Depends(general_rate_limit)]
)


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _asset_ref(asset: MediaAsset) -> ProjectAssetRef:
    """Signed URLs are minted per request and never stored.

    They expire in an hour, so a URL written into the timeline document, or
    cached anywhere, comes back dead on the next open. This is the same rule
    the media routes follow, for the same reason.
    """
    return ProjectAssetRef(
        id=ids.encode(ids.ASSET, asset.id),
        proxy_url=storage.presign_get(asset.proxy_key),
        peaks_url=storage.presign_get(asset.peaks_key),
        thumbnail_url=storage.presign_get(asset.thumbnail_key),
        duration_ms=asset.duration_ms,
    )


def _serialise(project: Project, assets: list[MediaAsset]) -> ProjectResponse:
    return ProjectResponse(
        id=ids.encode(ids.PROJECT, project.id),
        title=project.title,
        aspect_ratio=project.aspect_ratio,
        width=project.width,
        height=project.height,
        fps=project.fps,
        duration_ms=project.duration_ms,
        version=project.version,
        # Parsed back through the model rather than handed out as the raw
        # JSONB: a row written before a schema change is normalised on the way
        # out, so a client never has to cope with two shapes.
        timeline=TimelineDocument.model_validate(project.timeline),
        assets=[_asset_ref(a) for a in assets],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _summarise(project: Project) -> ProjectSummary:
    return ProjectSummary(
        id=ids.encode(ids.PROJECT, project.id),
        title=project.title,
        aspect_ratio=project.aspect_ratio,
        duration_ms=project.duration_ms,
        thumbnail_url=storage.presign_get(project.thumbnail_key),
        updated_at=project.updated_at,
    )


async def _load(
    project_id: str, user_id: uuid.UUID, session: Session
) -> tuple[ProjectRepository, Project]:
    projects = ProjectRepository(session, user_id)
    project = await projects.get_visible(ids.decode(ids.PROJECT, project_id))
    if project is None:
        raise NotFoundError("We have no project with that id.")
    return projects, project


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectResponse,
    summary="Start a project",
)
async def create_project(
    body: CreateProjectRequest,
    user: CurrentUser,
    session: Session,
) -> ProjectResponse:
    """contract §5: an empty timeline at `version: 0`.

    Canvas dimensions are derived from the aspect ratio here and never accepted
    from the client — a client that could name its own width could ask for 4K
    on a plan that does not include it simply by writing the numbers.
    """
    width, height = ASPECT_RATIOS[body.aspect_ratio]
    project = await ProjectRepository(session, user.id).create(
        title=body.title,
        aspect_ratio=body.aspect_ratio,
        width=width,
        height=height,
        timeline=empty_timeline().model_dump(mode="json", by_alias=True),
    )
    log.info("project_created", project_id=str(project.id), aspect_ratio=body.aspect_ratio)
    return _serialise(project, [])


@router.get("", response_model=Page[ProjectSummary], summary="The caller's projects")
async def list_projects(
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ProjectSummary]:
    """Summaries only — **no timelines**. Twenty projects carrying twenty
    documents would be megabytes to draw a page of titles."""
    rows, next_cursor = await ProjectRepository(session, user.id).page(limit=limit, cursor=cursor)
    return Page[ProjectSummary](items=[_summarise(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="One project, with its assets and fresh signed URLs",
)
async def get_project(project_id: str, user: CurrentUser, session: Session) -> ProjectResponse:
    """*"`assets` is a convenience […] so opening a project is one request
    rather than one per clip."*"""
    projects, project = await _load(project_id, user.id, session)
    assets = await projects.assets_for(project.id)
    return _serialise(project, assets)


@router.patch(
    "/{project_id}",
    response_model=ProjectSaveResponse,
    summary="Autosave the timeline, or rename the project",
)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    user: CurrentUser,
    session: Session,
) -> ProjectSaveResponse:
    projects, project = await _load(project_id, user.id, session)

    # Metadata first, and it never bumps `version` (contract §5). Bumping it
    # would turn a rename in one tab into a spurious 409 in every other one,
    # over an edit that cannot conflict with a timeline change.
    if body.title is not None or body.aspect_ratio is not None:
        await projects.update_metadata(
            project,
            title=body.title,
            aspect_ratio=body.aspect_ratio,
            canvas=ASPECT_RATIOS[body.aspect_ratio] if body.aspect_ratio else None,
        )

    if body.timeline is None:
        # A metadata-only PATCH, or an empty one. Reply in the same shape so the
        # client has one response to parse rather than two.
        return ProjectSaveResponse(
            version=project.version,
            duration_ms=project.duration_ms,
            updated_at=project.updated_at,
        )

    if body.version is None:
        raise InvalidTimelineError(
            "A timeline save must say which version it was made from.",
            details={"reason": "missingVersion"},
        )

    document = body.timeline

    # Structure first: it needs no database, so a document that is malformed on
    # its face costs no query at all.
    timeline_service.validate_structure(document)

    asset_ids = timeline_service.referenced_asset_ids(document)
    assets = await MediaAssetRepository(session, user.id).by_ids(asset_ids)
    timeline_service.validate_against_assets(document, assets)

    outcome = await projects.save_timeline(
        project.id,
        expected_version=body.version,
        timeline=document.model_dump(mode="json", by_alias=True),
        duration_ms=timeline_service.duration_ms(document),
    )
    if outcome is None:
        # Either the version moved under us, or the project was deleted between
        # the load above and the update. Tell those apart, because the client
        # can recover from one and not the other.
        current = await projects.current_version(project.id)
        if current is None:
            raise NotFoundError("We have no project with that id.")
        raise VersionConflictError(details={"currentVersion": current})

    # Only after the write is accepted, so a rejected save cannot leave the
    # side table describing a document that was never stored.
    await projects.replace_assets(project.id, asset_ids)

    log.info(
        "timeline_saved",
        project_id=str(project.id),
        version=outcome.version,
        duration_ms=outcome.duration_ms,
        assets=len(asset_ids),
    )
    return ProjectSaveResponse(
        version=outcome.version,
        duration_ms=outcome.duration_ms,
        updated_at=outcome.updated_at,
    )


@router.post(
    "/{project_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectResponse,
    summary="Copy a project",
)
async def duplicate_project(
    project_id: str, user: CurrentUser, session: Session
) -> ProjectResponse:
    """The timeline and the asset references, never the media — contract §5."""
    projects, project = await _load(project_id, user.id, session)
    copy = await projects.duplicate(project)
    assets = await projects.assets_for(copy.id)
    log.info("project_duplicated", source_id=str(project.id), project_id=str(copy.id))
    return _serialise(copy, assets)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project",
)
async def delete_project(project_id: str, user: CurrentUser, session: Session) -> Response:
    projects, project = await _load(project_id, user.id, session)
    await projects.soft_delete(project)
    log.info("project_deleted", project_id=str(project.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
