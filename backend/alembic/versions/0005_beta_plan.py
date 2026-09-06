"""M6 — the `beta` plan

$3.99 / ₹199, added beside the four tiers for the Discord launch and retired
later through `plans.is_public` rather than by deleting anything. Every value
and the reasoning for it is in docs/13-mvp-direction.md §3.

**Two things here look like defensive noise and are not.**

*`IF NOT EXISTS` on the label.* Migration `0002` builds every enum type from
`app.models.enums.ENUM_TYPES` — the *live* Python enum. So the moment `BETA`
was added to `PlanCode`, a database created from scratch got `plan_code` with
`beta` already in it, while an existing one did not. Without the guard this
migration would pass on a developer's machine and fail on a fresh CI database,
which is the worst way round.

*`AFTER 'free'`.* A bare `ADD VALUE` appends to the end of the label list, so a
migrated database would order the type `(free, pro, business, studio, beta)`
while a fresh one orders it `(free, beta, pro, business, studio)`. Nothing sorts
by `plans.code` today; the point is that nothing should have to check before it
does.

The `ALTER TYPE` runs in its own autocommit block because **Postgres refuses to
use a new enum label in the transaction that added it**. The seed below uses it,
so the two cannot share a transaction.

Revision ID: 0005_beta_plan
Revises: 0004_multipart_upload_id
Create Date: 2026-08-31

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_beta_plan"
down_revision: str | None = "0004_multipart_upload_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE plan_code ADD VALUE IF NOT EXISTS 'beta' AFTER 'free'")

    # `queue_priority` is 0 and not 5. Celery's `priority_steps` are
    # [0, 10, 20, 30] and `apply_async(priority=…)` passes this value straight
    # through — a number between bands is silently mapped to a neighbour, with
    # no error and no log. Queue priority is what Pro sells; this plan buys
    # volume, resolution and the absence of a watermark.
    #
    # `watermark` is 'none', and it is the most important value in the row:
    # removing the watermark is the main reason anyone converts off free, and a
    # paying user who still sees one churns in the first week.
    #
    # ON CONFLICT so re-running against a database that already carries the row
    # is a no-op rather than a failed deploy.
    op.execute(
        """
        INSERT INTO plans (
            code, display_name, monthly_credits, facemap_seconds, fair_use_credits,
            max_export_height, watermark, queue_priority, price_usd_cents, price_inr_paise,
            is_public
        ) VALUES
            ('beta', 'Beta', 800, 0, NULL, 1080, 'none', 0, 399, 19900, true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    """The row goes; the label stays.

    Postgres has no `ALTER TYPE … DROP VALUE`, and even if it did, dropping a
    label that a `subscriptions.plan` still references would fail. Retiring this
    plan for real is `UPDATE plans SET is_public = false` — which is not a
    downgrade, it is the plan's own lifecycle.
    """
    op.execute("DELETE FROM plans WHERE code = 'beta'")
