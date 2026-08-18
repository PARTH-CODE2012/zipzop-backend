"""Projects, scoped to their owner — contract §5.

Every query starts from `ScopedRepository._select()`, which already carries the
`user_id` filter; see `app/repositories/base.py` for why that is structural
rather than a habit.

The one method here that is not a plain query is `save_timeline`. Autosave
fires every two seconds from possibly more than one tab, so the version check
and the version bump have to be the same statement — see its docstring.
"""

import uuid
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from app.models import MediaAsset, Project, ProjectAsset
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ScopedRepository,
    decode_cursor,
    encode_cursor,
)


class SaveOutcome:
    """What `save_timeline` produced, or the fact that it produced nothing."""

    __slots__ = ("duration_ms", "updated_at", "version")

    def __init__(self, version: int, duration_ms: int, updated_at: Any) -> None:
        self.version = version
        self.duration_ms = duration_ms
        self.updated_at = updated_at


class ProjectRepository(ScopedRepository[Project]):
    model = Project

    def _visible(self) -> sa.sql.Select[Any]:
        return self._select().where(Project.deleted_at.is_(None))

    async def get_visible(self, project_id: uuid.UUID) -> Project | None:
        result = await self._session.execute(self._visible().where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        title: str,
        aspect_ratio: str,
        width: int,
        height: int,
        timeline: dict[str, Any],
    ) -> Project:
        project = Project(
            user_id=self.user_id,
            title=title,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            timeline=timeline,
            version=0,
            duration_ms=0,
        )
        self._session.add(project)
        await self._session.flush()
        return project

    async def page(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> tuple[list[Project], str | None]:
        """Most recently edited first — contract §5, *"newest first"*.

        Ordered by `updated_at` rather than `created_at`: a projects list is
        read to get back to what you were working on, and it matches the
        partial index the table already carries. The cursor helpers take a
        datetime and do not care which column it came from.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._visible()
        if cursor:
            moment, row_id = decode_cursor(cursor)
            query = query.where(
                sa.tuple_(Project.updated_at, Project.id)
                < sa.tuple_(sa.literal(moment), sa.literal(row_id))
            )
        query = query.order_by(Project.updated_at.desc(), Project.id.desc()).limit(limit + 1)
        rows = list((await self._session.execute(query)).scalars().all())

        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            return rows, encode_cursor(last.updated_at, last.id)
        return rows, None

    async def save_timeline(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        timeline: dict[str, Any],
        duration_ms: int,
    ) -> SaveOutcome | None:
        """Compare-and-set on `version`. Returns `None` when the version is stale.

        **The check and the bump are one statement on purpose.** Reading the
        version, comparing it in Python and then writing would leave a window
        in which two tabs both read 12, both decide they are current, and both
        write 13 — the second silently destroying the first's edit. That is
        precisely the failure `409 VERSION_CONFLICT` exists to make visible, so
        it cannot be allowed to happen underneath it.

        `updated_at` is set explicitly rather than left to the column's
        `onupdate`, because this is a Core statement and being explicit costs
        nothing.
        """
        statement = (
            sa.update(Project)
            .where(
                Project.id == project_id,
                Project.user_id == self.user_id,
                Project.deleted_at.is_(None),
                Project.version == expected_version,
            )
            .values(
                timeline=timeline,
                duration_ms=duration_ms,
                version=Project.version + 1,
                updated_at=sa.func.now(),
            )
            .returning(Project.version, Project.duration_ms, Project.updated_at)
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return SaveOutcome(version=row[0], duration_ms=row[1], updated_at=row[2])

    async def current_version(self, project_id: uuid.UUID) -> int | None:
        """For the `409` body: the client is told what the version actually is,
        so it can re-fetch without a second round trip to find out."""
        found = await self._session.scalar(
            sa.select(Project.version).where(
                Project.id == project_id,
                Project.user_id == self.user_id,
                Project.deleted_at.is_(None),
            )
        )
        return int(found) if found is not None else None

    async def update_metadata(
        self,
        project: Project,
        *,
        title: str | None,
        aspect_ratio: str | None,
        canvas: tuple[int, int] | None,
    ) -> None:
        """Title and canvas — contract §5: *"does not touch the timeline or bump
        `version`"*.

        Bumping it would make every other tab's next autosave a spurious 409
        over an edit that cannot conflict with a timeline change.
        """
        if title is not None:
            project.title = title
        if aspect_ratio is not None and canvas is not None:
            project.aspect_ratio = aspect_ratio
            project.width, project.height = canvas
        await self._session.flush()
        # `updated_at` carries `onupdate=func.now()`, which is a SQL expression:
        # after the flush the attribute is expired, and reading it to build the
        # response would trigger a lazy load with no greenlet around it. Refresh
        # it here, where the IO is awaited and obvious.
        await self._session.refresh(project, ["updated_at"])

    async def replace_assets(self, project_id: uuid.UUID, asset_ids: set[uuid.UUID]) -> None:
        """Rebuild `project_assets` from the document.

        Diffed rather than deleted-and-reinserted. Autosave runs every two
        seconds; churning every row of this table each time would write far
        more than it needs to, and the `RESTRICT` foreign key means those rows
        are what stops an asset being deleted out from under a project.
        """
        existing = set(
            (
                await self._session.execute(
                    sa.select(ProjectAsset.asset_id).where(ProjectAsset.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        if existing == asset_ids:
            return

        removed = existing - asset_ids
        if removed:
            await self._session.execute(
                sa.delete(ProjectAsset).where(
                    ProjectAsset.project_id == project_id,
                    ProjectAsset.asset_id.in_(removed),
                )
            )
        added = asset_ids - existing
        if added:
            self._session.add_all([ProjectAsset(project_id=project_id, asset_id=a) for a in added])
        await self._session.flush()

    async def assets_for(self, project_id: uuid.UUID) -> list[MediaAsset]:
        """The assets a project references, for the `assets` block of a GET.

        Read from `project_assets` rather than by walking the document: the
        side table is what the foreign key protects, so if the two ever
        disagree this returns the set that actually holds the media in place.
        """
        result = await self._session.execute(
            sa.select(MediaAsset)
            .join(ProjectAsset, ProjectAsset.asset_id == MediaAsset.id)
            .where(
                ProjectAsset.project_id == project_id,
                MediaAsset.user_id == self.user_id,
                MediaAsset.deleted_at.is_(None),
            )
            .order_by(MediaAsset.created_at.asc())
        )
        return list(result.scalars().all())

    async def duplicate(self, project: Project) -> Project:
        """Copy the document and the asset references, never the media.

        contract §5. The copy starts at `version: 0` — it has no edit history
        anyone could be holding a stale version of.
        """
        copy = Project(
            user_id=self.user_id,
            title=f"{project.title} (copy)"[:200],
            aspect_ratio=project.aspect_ratio,
            width=project.width,
            height=project.height,
            fps=project.fps,
            # Deep-copied, not shared. Both rows would otherwise hold the same
            # Python dict, and anything that mutated one in place would silently
            # edit the other. Nothing does today; this costs one call and removes
            # the trap rather than relying on that staying true.
            timeline=deepcopy(project.timeline),
            version=0,
            duration_ms=project.duration_ms,
            thumbnail_key=project.thumbnail_key,
        )
        self._session.add(copy)
        await self._session.flush()

        source_assets = set(
            (
                await self._session.execute(
                    sa.select(ProjectAsset.asset_id).where(ProjectAsset.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        if source_assets:
            self._session.add_all(
                [ProjectAsset(project_id=copy.id, asset_id=a) for a in source_assets]
            )
            await self._session.flush()
        return copy

    async def soft_delete(self, project: Project) -> None:
        """Soft delete, and `project_assets` stays.

        Leaving the rows means a deleted project still holds its media against
        the `RESTRICT`, so restoring one inside the retention window finds its
        footage intact rather than pointing at nothing.
        """
        project.deleted_at = sa.func.now()
        await self._session.flush()
