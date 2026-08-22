"""Every unit of server work: analysis, inference and export.

One table, one lifecycle, one progress mechanism, one notification path. That
uniformity is the design's main claim — later phases add workers, not
architecture (docs/03-backend-architecture.md §4.2, §5).

M2 creates the table so registration's ledger rows have somewhere to point and
so the ingest worker can be given a job row later. The job pipeline itself is
M4.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import JobFamily, JobStatus, JobTool, pg_enum
from app.models.mixins import UUIDPrimaryKey


class Job(UUIDPrimaryKey, Base):
    __tablename__ = "jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    tool: Mapped[JobTool] = mapped_column(pg_enum(JobTool, "job_tool"), nullable=False)
    family: Mapped[JobFamily] = mapped_column(pg_enum(JobFamily, "job_family"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        server_default=JobStatus.QUEUED.value,
    )
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    # Queue band, copied from the user's plan at creation rather than joined, so
    # a downgrade mid-job cannot demote work already queued.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Small results inline; anything over 256 KB goes to S3 and `result_key`
    # holds the object instead (contract §6.3).
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )

    credits_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    credits_settled: Mapped[int | None] = mapped_column(Integer, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_is_a_percentage"),
        # Replaying an idempotency key returns the original job instead of
        # charging the user twice (contract §1).
        Index(
            "uq_jobs_user_id_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
        Index("ix_jobs_user_id_created_at", "user_id", text("created_at DESC")),
        Index("ix_jobs_project_id", "project_id", postgresql_where="project_id IS NOT NULL"),
        Index(
            "ix_jobs_status_live",
            "status",
            postgresql_where="status IN ('queued', 'running')",
        ),
    )
