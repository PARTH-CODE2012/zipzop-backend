"""M6 — templates: the user's own settings, saved and reapplied

Decided 25 August as the **narrow** reading of "reuse"
(docs/13-mvp-direction.md §4): caption style, colour grade, transition
defaults, title styling — saved from one project and applied to another. Not a
supplied library, which carries a licensed music catalogue and the
naming-templates-after-real-people exposure, neither of which has an owner.

One table, and deliberately no more: **no worker, no queue, no credits, no new
job type.** A template is a subset of the timeline document, so it belongs
beside the editing operations rather than among the AI tools.

`settings` is JSONB rather than columns because it is a fragment of a document
whose shape the frontend owns and versions. Columns here would mean a migration
every time a style gained a field, for data the server never reads — it stores
and returns it, and the timeline invariants are checked where they already are.

Revision ID: 0007_templates
Revises: 0006_m6_billing
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_templates"
down_revision: str | None = "0006_m6_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # CITEXT so "Podcast" and "podcast" are one template. People name these
        # themselves and then re-save over them; two rows that look identical in
        # a list is a bug report.
        sa.Column("name", postgresql.CITEXT(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_templates_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_templates"),
        # Saving under a name that already exists **replaces** it, which is what
        # "save my settings as Podcast" means the second time somebody does it.
        # The constraint is what makes that an upsert rather than a duplicate.
        sa.UniqueConstraint("user_id", "name", name="uq_templates_user_id_name"),
    )
    op.create_index("ix_templates_user_id_name", "templates", ["user_id", "name"])


def downgrade() -> None:
    op.drop_index("ix_templates_user_id_name", table_name="templates")
    op.drop_table("templates")
