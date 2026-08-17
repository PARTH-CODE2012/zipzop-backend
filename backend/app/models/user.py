"""Accounts and the refresh-token rotation chain.

Schema per docs/03-backend-architecture.md §4.2.
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
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import UserStatus, pg_enum
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey


class User(UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    # CITEXT, so Sam@example.com and sam@example.com are one account.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        nullable=False,
        server_default=UserStatus.ACTIVE.value,
    )

    # Cached projections of credit_ledger, one per bucket. Never write one of
    # these without a matching ledger row in the same transaction — the nightly
    # reconciliation (M6) exists to catch anyone who does.
    plan_credits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    topup_credits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    facemap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # BIGINT: a single account can hold more than 2 GB, which is where INTEGER stops.
    storage_bytes_used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint("plan_credits >= 0", name="plan_credits_non_negative"),
        CheckConstraint("topup_credits >= 0", name="topup_credits_non_negative"),
        CheckConstraint("facemap_seconds >= 0", name="facemap_seconds_non_negative"),
    )

    @property
    def total_credits(self) -> int:
        """What the user can spend right now — the number to show most
        prominently (docs/05-api-contract.md §2)."""
        return self.plan_credits + self.topup_credits


class RefreshToken(UUIDPrimaryKey, Base):
    """One row per issued refresh token.

    Only the SHA-256 of the token is stored: a database dump must not be a bag
    of working sessions. Rotation is enforced through `replaced_by` — see
    `app.services.auth` for the reuse-detection rule.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("refresh_tokens.id"),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Partial: the only lookup is "tokens still live for this user".
        Index(
            "ix_refresh_tokens_user_id_live",
            "user_id",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.replaced_by is None
