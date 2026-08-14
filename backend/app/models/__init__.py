"""SQLAlchemy models.

Every model must be imported here. Alembic's autogenerate only sees what has
been imported into Base.metadata — a model that is not listed will silently
produce a migration that drops its table.

Tables land in M2, in the order of docs/03-backend-architecture.md §4.2:
users, refresh_tokens, media_assets, projects, project_assets, jobs, plans,
subscriptions, payments, provider_events, credit_ledger.
"""

from app.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKey

__all__ = ["Base", "TimestampMixin", "UUIDPrimaryKey"]
