"""Projects, and the side table that makes the JSONB timeline safe.

The timeline is a document, not rows — the reasoning is in
docs/03-backend-architecture.md §4.1. The consequence to accept is that
Postgres cannot validate it, so the application must, on every write, against
docs/05-api-contract.md §4.3.

M2 creates these tables but no endpoint writes them: the milestone's timeline
lives in the browser only. Persistence is M3, which is why the validation and
the `project_assets` rebuild are not here yet.
"""

import uuid
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
)  # `text` builds both the JSONB default and the DESC index expressions
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey


class Project(UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "projects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="Untitled project")

    # ------------------------------------------------------------- canvas
    aspect_ratio: Mapped[str] = mapped_column(Text, nullable=False, server_default="9:16")
    width: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1080")
    height: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1920")
    fps: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="30")

    # ----------------------------------------------------------- timeline
    timeline: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{\"tracks\": []}'::jsonb"),
    )

    # Optimistic concurrency. Incremented on every accepted write; a PATCH
    # carrying a stale version is rejected with 409 rather than merged.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    thumbnail_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_projects_user_id_updated_at",
            "user_id",
            text("updated_at DESC"),
            postgresql_where="deleted_at IS NULL",
        ),
    )


class ProjectAsset(Base):
    """Which assets a project's timeline references.

    Rebuilt by the API on every timeline write by walking the document. The
    `RESTRICT` is deliberate: an asset still used by a project cannot be
    deleted out from under it, and the API turns that into `ASSET_IN_USE`
    rather than a timeline that points at nothing.
    """

    __tablename__ = "project_assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        primary_key=True,
    )

    __table_args__ = (Index("ix_project_assets_asset_id", "asset_id"),)
