"""Remember which S3 multipart upload an asset is being assembled from

`POST /media/{id}/complete` has to hand S3 the upload id to assemble the parts.
It was passing `body.etag` — a real field, the wrong thing entirely — so every
multipart completion failed with `NoSuchUpload`. Nothing on the request or the
row carried the upload id at all, so there was no correct value available to
pass.

One nullable column, written at reservation. The alternative was adding an
`uploadId` to `CompleteUploadRequest` and having the client echo it back, which
changes the API contract and means trusting a client-supplied identifier for an
upload the server started itself. Keeping it server-side also makes a replayed
reservation return the *same* upload with fresh part URLs rather than starting
a second one, and gives `abort_multipart` — defined since M2 and never callable
— the id it needs to reclaim the parts of an upload nobody finished.

Nullable with no default and no backfill: every existing row predates any
multipart upload that could still be completed (the part URLs expire in fifteen
minutes), so `NULL` is both accurate and the value the completion path already
treats as "this was not a multipart upload".

Revision ID: 0004_multipart_upload_id
Revises: 0003_media_asset_claim
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_multipart_upload_id"
down_revision: str | None = "0003_media_asset_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("multipart_upload_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "multipart_upload_id")
