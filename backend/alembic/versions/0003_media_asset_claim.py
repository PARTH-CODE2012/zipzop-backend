"""Give media_assets the atomic claim jobs already has

The gap named and deliberately left open by the 26 August reliability pass
(docs/16-pipeline-reliability-notes.md §5): `jobs` has `WHERE status='queued'`
in its claiming UPDATE, so two workers can never both pick up the same job and
re-sending a Celery message for one is a harmless no-op. `media_assets` had no
equivalent — `run_ingest` read the row and started transcoding — so the
pipeline sweep could only *report* a stuck `probing` asset. Re-triggering
ingest could have started a second worker transcoding and uploading the same
file while the first was still running, racing the same row's `finish_ingest`
write.

Three columns, mirroring `jobs.worker_id`, `jobs.started_at` and
`jobs.attempts`:

* `worker_id` — the discriminator the claim tests. An asset is already
  `probing` when its message is sent, with no `queued` state before it, so the
  status alone cannot separate "waiting for a worker" from "a worker has it".
* `ingest_started_at` — when a worker claimed it, as distinct from `created_at`,
  which is when the upload was reserved. The sweep needs both: `created_at` is
  how it finds an asset whose message never arrived, `ingest_started_at` is how
  it finds one whose worker died.
* `ingest_attempts` — so a file that kills its worker every time is failed
  rather than retried for ever.

All three are nullable or defaulted, so this applies to a live table without a
rewrite and without a backfill. Existing `probing` rows come out with
`worker_id IS NULL` and `ingest_started_at IS NULL`, which reads exactly as
"never claimed" — the sweep's own first case, and the correct interpretation
for a row that predates the claim.

Revision ID: 0003_media_asset_claim
Revises: 0002_m2_schema
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_media_asset_claim"
down_revision: str | None = "0002_m2_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("worker_id", sa.Text(), nullable=True))
    op.add_column(
        "media_assets",
        sa.Column("ingest_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "ingest_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    # The mirror of `ix_jobs_status_live`. Partial, because `probing` is a
    # handful of rows at any moment against a table that only grows.
    op.create_index(
        "ix_media_assets_status_probing",
        "media_assets",
        ["status"],
        postgresql_where=sa.text("status = 'probing'"),
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_status_probing", table_name="media_assets")
    op.drop_column("media_assets", "ingest_attempts")
    op.drop_column("media_assets", "ingest_started_at")
    op.drop_column("media_assets", "worker_id")
