"""A user's saved editing settings.

The narrow reading of "templates", decided 25 August
(docs/13-mvp-direction.md §4): caption style, colour grade, transition
defaults, title styling — **the user's own settings**, saved from one project
and reapplied to another. Not a supplied library.

`settings` is a JSON fragment of the timeline document and the server does not
interpret it. That is the point: the document's shape is owned and versioned by
the editor, and columns here would mean a migration every time a style gained a
field the backend never reads. What the server owns — that it belongs to this
user, and that a name is unique within the account — is in the schema.
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKey


class Template(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "templates"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: CITEXT, so "Podcast" and "podcast" are one template. People name these
    #: themselves and then save over them; two rows that read identically in a
    #: list is a bug report.
    name: Mapped[str] = mapped_column(CITEXT, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_templates_user_id_name"),
        Index("ix_templates_user_id_name", "user_id", "name"),
    )
