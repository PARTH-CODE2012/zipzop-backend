"""Credits revoked when their payment is refunded

The project lead's rule, 8 October 2026: a refund or a lost dispute takes back
the credits the payment granted, and a customer who already spent some keeps
what they spent. The rest is clawed back. The ledger records each such row
under its own reason, `payment_reversal`.

`IF NOT EXISTS`, and in an autocommit block, for the reasons `0005_beta_plan`
gives: migration `0002` builds `ledger_reason` from the live Python enum, so a
fresh database already has the label, and Postgres refuses to use a label in
the transaction that added it.

Revision ID: 0010_payment_reversal_reason
Revises: 0009_promo_violations
Create Date: 2026-10-08

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_payment_reversal_reason"
down_revision: str | None = "0009_promo_violations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ledger_reason ADD VALUE IF NOT EXISTS 'payment_reversal'")


def downgrade() -> None:
    # Postgres cannot drop a label from an enum type, and rows may use it. The
    # label is harmless unused, so a downgrade leaves it in place.
    pass
