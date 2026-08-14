"""baseline — extensions only

Deliberately empty of tables. It exists to prove the migration chain runs end
to end before any schema depends on it, and to install the extensions every
later migration assumes are present.

Tables arrive in M2, in the order of docs/03-backend-architecture.md §4.2.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-13

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # gen_random_uuid() for primary keys.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # Case-insensitive email, so Sam@example.com and sam@example.com are one
    # account rather than two.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    # pgvector is phase 2 (face embeddings). Not installed yet — it is not a
    # core extension and adding it here would make local setup fail for
    # something nothing uses until then.


def downgrade() -> None:
    # Extensions are left in place. Dropping them would break any other
    # database object that came to depend on them, and they cost nothing.
    pass
