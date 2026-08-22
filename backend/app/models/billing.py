"""Plans, subscriptions, payments and the provider event log.

M2 creates these tables and seeds the plan catalogue, because registration has
to put the new account on the free plan and grant its allowance. Everything
else here is M6 — no code writes a payment in phase 1's second milestone.

A tier is a row, not a branch in code: pricing changes more often than anything
else in the system, and a price change should not require a deploy
(docs/03-backend-architecture.md §4.2).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import (
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    PlanCode,
    SubStatus,
    WatermarkMode,
    pg_enum,
)
from app.models.mixins import TimestampMixin, UUIDPrimaryKey


class Plan(Base):
    __tablename__ = "plans"

    code: Mapped[PlanCode] = mapped_column(pg_enum(PlanCode, "plan_code"), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)

    monthly_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    facemap_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL except on tiers advertised as unlimited, where it is the hard ceiling.
    fair_use_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)

    max_export_height: Mapped[int] = mapped_column(Integer, nullable=False)
    watermark: Mapped[WatermarkMode] = mapped_column(
        pg_enum(WatermarkMode, "watermark_mode"), nullable=False
    )
    queue_priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Minor units — cents and paise. Never floats, and never one column for two
    # currencies without the currency beside it.
    price_usd_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_inr_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Subscription(UUIDPrimaryKey, TimestampMixin, Base):
    """Every user has one, including free users.

    Uniform rows mean the renewal and grant logic has no special case for the
    free tier — free users get their allowance through exactly the same path as
    paying ones.
    """

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan: Mapped[PlanCode] = mapped_column(
        pg_enum(PlanCode, "plan_code"), ForeignKey("plans.code"), nullable=False
    )
    status: Mapped[SubStatus] = mapped_column(
        pg_enum(SubStatus, "sub_status"),
        nullable=False,
        server_default=SubStatus.ACTIVE.value,
    )

    provider: Mapped[PaymentProvider | None] = mapped_column(
        pg_enum(PaymentProvider, "payment_provider"), nullable=True
    )
    provider_customer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)

    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    __table_args__ = (
        # One live subscription per user, whichever provider it came through.
        # This is what stops someone holding a Stripe and a Razorpay plan at once.
        Index(
            "one_live_subscription",
            "user_id",
            unique=True,
            postgresql_where="status IN ('active', 'past_due')",
        ),
        Index(
            "uq_subscriptions_provider_provider_subscription_id",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where="provider_subscription_id IS NOT NULL",
        ),
        # Drives the renewal sweep.
        Index(
            "ix_subscriptions_current_period_end",
            "current_period_end",
            postgresql_where="status IN ('active', 'past_due')",
        ),
    )


class Payment(UUIDPrimaryKey, Base):
    __tablename__ = "payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    provider: Mapped[PaymentProvider] = mapped_column(
        pg_enum(PaymentProvider, "payment_provider"), nullable=False
    )
    provider_payment_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[PaymentKind] = mapped_column(pg_enum(PaymentKind, "payment_kind"), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        nullable=False,
        server_default=PaymentStatus.PENDING.value,
    )

    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    credits_granted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_payments_provider_provider_payment_id",
            "provider",
            "provider_payment_id",
            unique=True,
        ),
    )


class ProviderEvent(Base):
    """Raw webhook deliveries, stored before they are processed.

    Both providers retry, sometimes for days, and both can deliver out of
    order. The handler inserts first and processes second: a duplicate delivery
    collides on the primary key and is acknowledged without being replayed.
    """

    __tablename__ = "provider_events"

    provider: Mapped[PaymentProvider] = mapped_column(
        pg_enum(PaymentProvider, "payment_provider"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
