"""Media assets, scoped to their owner.

Everything here goes through `ScopedRepository._select()`, so no query can
reach another account's rows — see `app/repositories/base.py` for why that is
structural rather than a convention.
"""

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from app.models import AssetKind, AssetStatus, MediaAsset, Project, ProjectAsset
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ScopedRepository,
    decode_cursor,
    encode_cursor,
)


class MediaAssetRepository(ScopedRepository[MediaAsset]):
    model = MediaAsset

    def _visible(self) -> sa.sql.Select[Any]:
        """Soft-deleted rows are gone as far as the API is concerned."""
        return self._select().where(MediaAsset.deleted_at.is_(None))

    async def get_visible(self, asset_id: uuid.UUID) -> MediaAsset | None:
        result = await self._session.execute(self._visible().where(MediaAsset.id == asset_id))
        return result.scalar_one_or_none()

    async def by_ids(self, asset_ids: set[uuid.UUID]) -> dict[uuid.UUID, MediaAsset]:
        """The caller's assets among those ids, keyed by id.

        Timeline validation calls this once per save rather than once per clip:
        a caption run puts thousands of clips on the timeline, but they point at
        a handful of assets, and one `IN` is the difference between a save that
        is a query and a save that is a thousand.

        **Absence carries the ownership check.** Invariant 5 is satisfied by a
        referenced id simply not appearing in the returned mapping, which is why
        this goes through `_visible()` and not a bare select.
        """
        if not asset_ids:
            return {}
        result = await self._session.execute(self._visible().where(MediaAsset.id.in_(asset_ids)))
        return {asset.id: asset for asset in result.scalars().all()}

    async def create_pending(
        self,
        *,
        kind: AssetKind,
        storage_key: str,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> MediaAsset:
        asset = MediaAsset(
            user_id=self.user_id,
            kind=kind,
            status=AssetStatus.PENDING_UPLOAD,
            storage_key=storage_key,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def page(
        self,
        *,
        kind: AssetKind | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[MediaAsset], str | None]:
        """Newest first, cursor-paged.

        Contract §1: never offsets — a list that changes while being paged
        skips rows, and a media bin changes every time an upload finishes.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._visible()
        if kind is not None:
            query = query.where(MediaAsset.kind == kind)
        if cursor:
            created_at, row_id = decode_cursor(cursor)
            # Strict tuple comparison, so the row the cursor names is the last
            # one on the previous page rather than the first of this one.
            query = query.where(
                sa.tuple_(MediaAsset.created_at, MediaAsset.id)
                < sa.tuple_(sa.literal(created_at), sa.literal(row_id))
            )

        query = query.order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc()).limit(limit + 1)
        rows = list((await self._session.execute(query)).scalars().all())

        # One row over the page size is how we know there is a next page
        # without a second COUNT query.
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            return rows, encode_cursor(last.created_at, last.id)
        return rows, None

    async def projects_using(self, asset_id: uuid.UUID) -> list[uuid.UUID]:
        """Which of the caller's projects reference this asset.

        Drives the `ASSET_IN_USE` detail: the contract returns the project ids
        so the interface can say *which* projects, not just that there are
        some.
        """
        result = await self._session.execute(
            sa.select(ProjectAsset.project_id)
            .join(Project, Project.id == ProjectAsset.project_id)
            .where(
                ProjectAsset.asset_id == asset_id,
                Project.user_id == self.user_id,
                Project.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def storage_bytes_used(self) -> int:
        """Live sum over the caller's assets.

        `users.storage_bytes_used` is a cached projection kept for display;
        the quota check reads the truth, because a stale cache is the
        difference between a full account and an unbounded one.
        """
        total = await self._session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(MediaAsset.size_bytes), 0)).where(
                MediaAsset.user_id == self.user_id,
                MediaAsset.deleted_at.is_(None),
            )
        )
        return int(total or 0)

    async def mark_probing(
        self, asset: MediaAsset, *, size_bytes: int, checksum: str | None
    ) -> None:
        asset.status = AssetStatus.PROBING
        asset.size_bytes = size_bytes
        if checksum:
            asset.checksum_sha256 = checksum
        await self._session.flush()

    async def soft_delete(self, asset: MediaAsset) -> None:
        asset.status = AssetStatus.DELETED
        asset.deleted_at = sa.func.now()
        await self._session.flush()


async def finish_ingest(
    session: Any,
    asset_id: uuid.UUID,
    *,
    duration_ms: int,
    width: int | None,
    height: int | None,
    fps: Decimal | None,
    video_codec: str | None,
    audio_codec: str | None,
    audio_channels: int | None,
    sample_rate: int | None,
    proxy_key: str | None,
    thumbnail_key: str | None,
    peaks_key: str | None,
) -> None:
    """Write the probe results and the derivative keys, then decide `ready`.

    Not a repository method: the worker has no request and therefore no
    authenticated user to scope to. It is given the asset id by the task that
    the API itself enqueued, which is where the authorisation happened.
    """
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:  # pragma: no cover - the row is deleted mid-ingest
        return

    asset.duration_ms = duration_ms
    asset.width = width
    asset.height = height
    asset.fps = fps
    asset.video_codec = video_codec
    asset.audio_codec = audio_codec
    asset.audio_channels = audio_channels
    asset.sample_rate = sample_rate
    asset.proxy_key = proxy_key
    asset.thumbnail_key = thumbnail_key
    asset.peaks_key = peaks_key

    # `ready` is a conclusion, never an assignment: it is true exactly when
    # every derivative the kind needs is on the row.
    asset.status = AssetStatus.READY if asset.has_all_derivatives else AssetStatus.FAILED
    if asset.status is AssetStatus.FAILED:
        asset.failure_reason = "Some previews could not be generated for this file."
    await session.flush()


async def fail_ingest(session: Any, asset_id: uuid.UUID, reason: str) -> None:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:  # pragma: no cover
        return
    asset.status = AssetStatus.FAILED
    asset.failure_reason = reason
    await session.flush()
