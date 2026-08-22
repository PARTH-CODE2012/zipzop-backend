"""The credit ledger. Append-only: never updated, never deleted.

Every row names the **bucket** it moved. The buckets expire on different
schedules, and a balance that cannot say which credits it holds cannot expire
them correctly (docs/03-backend-architecture.md §5.4).

M2 writes exactly one kind of row — the `signup_grant` at registration. Reserve,
refund and the monthly grant arrive with the job pipeline and billing.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import CreditBucket, LedgerReason, pg_enum


class CreditLedgerEntry(Base):
    __tablename__ = "credit_ledger"

    # BIGSERIAL: this table only ever grows, and it grows per job.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket: Mapped[CreditBucket] = mapped_column(
        pg_enum(CreditBucket, "credit_bucket"), nullable=False
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[LedgerReason] = mapped_column(
        pg_enum(LedgerReason, "ledger_reason"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Running balance of THIS bucket, so the ledger can be read without
    # replaying it from the beginning.
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # A zero-delta row records nothing and would only make the ledger
        # harder to read.
        CheckConstraint("delta <> 0", name="delta_is_never_zero"),
        Index("ix_credit_ledger_user_id_created_at", "user_id", text("created_at DESC")),
        # One reserve and at most one refund per job PER BUCKET. A single job
        # may draw from two buckets, so the bucket is part of the key. This is
        # the guard that makes a double refund impossible even if a worker
        # retries its completion handler.
        Index(
            "uq_credit_ledger_job_id_reason_bucket",
            "job_id",
            "reason",
            "bucket",
            unique=True,
            postgresql_where="job_id IS NOT NULL",
        ),
    )
