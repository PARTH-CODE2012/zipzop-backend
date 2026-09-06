"""M6 — provider plan ids, promo codes, attribution and the commission ledger

Four additions, and none of them changes how money already moves. The credit
ledger, the buckets and the reserve/refund rules are untouched; what arrives
here is a counterparty (the Discord server owner), a permanent attribution, and
the mapping a provider needs to be told which plan is which.

**`ALTER TYPE … ADD VALUE` and `CREATE TYPE` are both written to survive being
run against a database that already has them.** Migration `0002` builds every
enum from the live `app.models.enums.ENUM_TYPES`, so the moment a member is
added in Python, a *fresh* database gets it and an existing one does not. A
migration without the guards passes on a developer's machine and fails on CI —
see `0005_beta_plan` for the same trap and the same fix.

Revision ID: 0006_m6_billing
Revises: 0005_beta_plan
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.enums import CommissionReason, enum_labels

revision: str = "0006_m6_billing"
down_revision: str | None = "0005_beta_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    # ------------------------------------------------------------ new labels
    #
    # In its own autocommit block because Postgres refuses to *use* a new enum
    # label in the transaction that added it, and the tables below use them.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE ledger_reason ADD VALUE IF NOT EXISTS 'promo_grant' AFTER 'signup_grant'"
        )

    # `CREATE TYPE` has no `IF NOT EXISTS`. The DO block is the idiomatic
    # equivalent, and is here for the same fresh-versus-migrated reason as the
    # guard above.
    labels = ", ".join(f"'{label}'" for label in enum_labels(CommissionReason))
    op.execute(
        f"""
        DO $$ BEGIN
            CREATE TYPE commission_reason AS ENUM ({labels});
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # ------------------------------------------------------- provider_plans
    #
    # Razorpay and Stripe both want a plan created on *their* side before a
    # subscription can reference it, and both give it an opaque id. This is the
    # mapping, keyed by currency as well as plan because a provider plan holds
    # one price in one currency.
    #
    # A table rather than configuration: the ids are created lazily on first
    # checkout, so they cannot be known when the environment is written, and an
    # id that lives only in an env var is one deploy away from being lost.
    op.create_table(
        "provider_plans",
        sa.Column("provider", _enum("payment_provider"), nullable=False),
        sa.Column("plan_code", _enum("plan_code"), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("provider_plan_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["plan_code"], ["plans.code"], name="fk_provider_plans_plan_code_plans"
        ),
        sa.PrimaryKeyConstraint("provider", "plan_code", "currency", name="pk_provider_plans"),
    )

    # ----------------------------------------------------------- promo_codes
    #
    # One row per Discord server owner. `is_active` retires a code without
    # deleting it, for the same reason `plans.is_public` retires a plan: the
    # attribution rows that point at it have to keep resolving.
    op.create_table(
        "promo_codes",
        sa.Column("code", postgresql.CITEXT(), nullable=False),
        sa.Column("owner_label", sa.Text(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bonus_credits", sa.Integer(), nullable=False, server_default="300"),
        # Basis points, not a percent: 15% is 1500 and never 0.15. A rate held
        # as a float is a rounding argument with a server owner waiting for it.
        sa.Column("commission_bps", sa.Integer(), nullable=False, server_default="1500"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("bonus_credits >= 0", name="ck_promo_codes_bonus_credits_non_negative"),
        sa.CheckConstraint(
            "commission_bps BETWEEN 0 AND 10000", name="ck_promo_codes_commission_bps_is_a_rate"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_promo_codes_owner_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("code", name="pk_promo_codes"),
    )

    # ------------------------------------------------------- attribution
    #
    # **On the user, permanently.** The commission is owed on a subscription
    # that happens later — sometimes months later — so an attribution held in
    # the session is a commission that can never be paid
    # (docs/13-mvp-direction.md §6).
    op.add_column("users", sa.Column("promo_code", postgresql.CITEXT(), nullable=True))
    op.add_column(
        "users", sa.Column("promo_code_applied_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_users_promo_code_promo_codes",
        "users",
        "promo_codes",
        ["promo_code"],
        ["code"],
        ondelete="SET NULL",
    )
    # Partial: the only query is "everyone this code brought in".
    op.create_index(
        "ix_users_promo_code",
        "users",
        ["promo_code"],
        postgresql_where=sa.text("promo_code IS NOT NULL"),
    )

    # ------------------------------------------------- the commission ledger
    #
    # The same shape as `credit_ledger` and for the same reasons: append-only,
    # signed, and with a unique index that makes a double accrual impossible
    # rather than merely unlikely. A webhook redelivered a week later cannot pay
    # a server owner twice, because the second insert collides.
    op.create_table(
        "commission_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", postgresql.CITEXT(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", _enum("commission_reason"), nullable=False),
        # Minor units, like every other money column here. Signed: an accrual
        # is positive, a payout and a reversal are negative.
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rate_bps", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("amount_minor <> 0", name="ck_commission_ledger_amount_is_never_zero"),
        sa.ForeignKeyConstraint(
            ["code"], ["promo_codes.code"], name="fk_commission_ledger_code_promo_codes"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_commission_ledger_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payments.id"], name="fk_commission_ledger_payment_id_payments",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commission_ledger"),
    )
    op.create_index(
        "ix_commission_ledger_code_created_at",
        "commission_ledger",
        ["code", sa.text("created_at DESC")],
    )
    # One accrual, and at most one payout, per payment. This is the guard, and
    # it is a database constraint rather than a check in code for exactly the
    # reason the credit ledger's is: code that relies on remembering to check
    # is code that will one day forget.
    op.create_index(
        "uq_commission_ledger_payment_id_reason",
        "commission_ledger",
        ["payment_id", "reason"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NOT NULL"),
    )

    # --------------------------------------------------- deferred downgrades
    #
    # §8.3: upgrades apply immediately, **downgrades at the next period
    # boundary**, so nobody loses credits they are half way through using.
    # Deferring needs somewhere to keep what to switch to.
    op.add_column("subscriptions", sa.Column("pending_plan", _enum("plan_code"), nullable=True))
    op.create_foreign_key(
        "fk_subscriptions_pending_plan_plans",
        "subscriptions",
        "plans",
        ["pending_plan"],
        ["code"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_subscriptions_pending_plan_plans", "subscriptions", type_="foreignkey")
    op.drop_column("subscriptions", "pending_plan")

    op.drop_table("commission_ledger")

    op.drop_index("ix_users_promo_code", table_name="users")
    op.drop_constraint("fk_users_promo_code_promo_codes", "users", type_="foreignkey")
    op.drop_column("users", "promo_code_applied_at")
    op.drop_column("users", "promo_code")

    op.drop_table("promo_codes")
    op.drop_table("provider_plans")

    op.execute("DROP TYPE IF EXISTS commission_reason")
    # `promo_grant` stays on `ledger_reason`, because Postgres has no
    # `ALTER TYPE … DROP VALUE` and any ledger row using it would block one
    # anyway. An unused label costs nothing.
