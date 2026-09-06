"""M6 — record how much media a job actually chewed through

🟠 **The number that decides whether the `beta` plan makes money.**

Every tier's allowance derives from `SECONDS_PER_MINUTE_OF_MEDIA` in
`pricing.py`, which docs/11-m4-notes.md §8 flags as *a heuristic, not a
measurement*. At $19.99 an error there was absorbed. At $3.99 — net of the 15%
commission and processing, about **$3.28** — it is not.

`jobs` already carries `started_at`, `finished_at`, `tool` and
`credits_reserved`, so the only thing missing to turn the heuristic into a
measurement is **how much media the job processed**. One column, written when
the job is priced, because that is the moment the number is already known: the
quote computes it to charge for it.

Reconstructing it later would mean joining to `media_assets` or `projects` and
guessing at the range the job was given — for rows whose asset may since have
been deleted. A column that is free to write and impossible to lose is worth
more than a query that is usually right.

Revision ID: 0008_job_media_duration
Revises: 0007_templates
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_job_media_duration"
down_revision: str | None = "0007_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and left NULL for every job that already ran. A backfilled guess
    # would be indistinguishable from a measurement in the report this feeds,
    # which is the one thing that report must not contain.
    op.add_column("jobs", sa.Column("media_duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "media_duration_ms")
