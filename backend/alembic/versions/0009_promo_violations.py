"""Referral violations — the project lead's graduated policy

The lead's answer to docs/24-m7-closure.md §7 item 4, given on 6 October 2026:
using your own code, or sharing it outside your Discord server, is a violation.
The first cancels the commission it earned and is a warning. The second removes
the code.

That needs the violations to be countable and explainable afterwards, so this is
a table of decisions, append-only like the ledgers. It holds what was found,
against which account, and what was done about it.

`kind` and `action` are text with a check constraint, not native enums. A third
kind is then one `ALTER TABLE`, without the enum-type guards `0005_beta_plan`
needed.

Revision ID: 0009_promo_violations
Revises: 0008_job_media_duration
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_promo_violations"
down_revision: str | None = "0008_job_media_duration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "promo_violations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", postgresql.CITEXT(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("referred_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('self_use', 'shared_outside_server')",
            name="ck_promo_violations_kind_is_known",
        ),
        sa.CheckConstraint(
            "action IN ('warning', 'code_removed')",
            name="ck_promo_violations_action_is_known",
        ),
        sa.ForeignKeyConstraint(
            ["code"], ["promo_codes.code"], name="fk_promo_violations_code_promo_codes"
        ),
        sa.ForeignKeyConstraint(
            ["referred_user_id"],
            ["users.id"],
            name="fk_promo_violations_referred_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_promo_violations"),
    )
    # "How many times has this owner been warned" is the only query.
    op.create_index(
        "ix_promo_violations_code_created_at", "promo_violations", ["code", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_promo_violations_code_created_at", table_name="promo_violations")
    op.drop_table("promo_violations")
