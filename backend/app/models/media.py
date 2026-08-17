"""Uploaded and derived media.

Immutable once ready: nothing overwrites an upload. A job that changes pixels
produces a *new* asset pointing back at its source, which is what makes
"revert to original" free (docs/03-backend-architecture.md §6.4, principle 4).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import AssetKind, AssetStatus, pg_enum
from app.models.mixins import UUIDPrimaryKey


class MediaAsset(UUIDPrimaryKey, Base):
    __tablename__ = "media_assets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[AssetKind] = mapped_column(pg_enum(AssetKind, "asset_kind"), nullable=False)
    status: Mapped[AssetStatus] = mapped_column(
        pg_enum(AssetStatus, "asset_status"),
        nullable=False,
        server_default=AssetStatus.PENDING_UPLOAD.value,
    )

    # ------------------------------------------------------------- storage
    # Keys, not URLs. URLs are signed at read time and expire in an hour, so
    # persisting one would hand out a link that is dead before it is used.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    peaks_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -------------------------------------------------------------- probe
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NUMERIC, not float: 29.97 and 30000/1001 must round-trip without drifting.
    fps: Mapped[Decimal | None] = mapped_column(Numeric(7, 3), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------------------------------------------------------- provenance
    derived_from_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The constraint is declared here so the metadata matches the final schema,
    # but the migration adds it with an ALTER after `jobs` exists — this table
    # is created first, and jobs points back at it through `output_asset_id`.
    derived_by_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # `text("created_at DESC")`, not `postgresql_ops` — that option carries
        # operator classes, not sort order, and using it here would leave the
        # index ascending while the model claimed otherwise.
        Index(
            "ix_media_assets_user_id_created_at",
            "user_id",
            text("created_at DESC"),
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "ix_media_assets_derived_from_asset_id",
            "derived_from_asset_id",
            postgresql_where="derived_from_asset_id IS NOT NULL",
        ),
        # Lets a re-upload of the same bytes reuse the existing asset rather
        # than storing it twice.
        Index(
            "ix_media_assets_user_id_checksum_sha256",
            "user_id",
            "checksum_sha256",
            postgresql_where="status = 'ready'",
        ),
    )

    @property
    def is_ready(self) -> bool:
        return self.status is AssetStatus.READY

    @property
    def has_all_derivatives(self) -> bool:
        """The gate for `ready` (docs/03-backend-architecture.md §6.2).

        For video that is all four outputs: probe, proxy, thumbnail, peaks.
        `duration_ms` stands in for the probe — nothing else is written until
        ffprobe has answered.

        The other kinds have fewer, and demanding four would leave them stuck
        in `probing` forever: audio has no picture to scale or thumbnail, and a
        still image has no waveform.
        """
        if self.duration_ms is None:
            return False
        if self.kind is AssetKind.VIDEO:
            return all(
                (
                    self.proxy_key is not None,
                    self.thumbnail_key is not None,
                    self.peaks_key is not None,
                )
            )
        if self.kind is AssetKind.AUDIO:
            return self.peaks_key is not None
        return self.thumbnail_key is not None
