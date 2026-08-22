"""SQLAlchemy models.

Every model must be imported here. Alembic's autogenerate only sees what has
been imported into Base.metadata — a model that is not listed will silently
produce a migration that drops its table.

Tables land in M2, in the order of docs/03-backend-architecture.md §4.2:
users, refresh_tokens, media_assets, projects, project_assets, jobs, plans,
subscriptions, payments, provider_events, credit_ledger.
"""

from app.db import Base
from app.models.billing import Payment, Plan, ProviderEvent, Subscription
from app.models.credit import CreditLedgerEntry
from app.models.enums import (
    ENUM_TYPES,
    AssetKind,
    AssetStatus,
    CreditBucket,
    JobFamily,
    JobStatus,
    JobTool,
    LedgerReason,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    PlanCode,
    SubStatus,
    UserStatus,
    WatermarkMode,
    enum_labels,
)
from app.models.job import Job
from app.models.media import MediaAsset
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey
from app.models.project import Project, ProjectAsset
from app.models.user import RefreshToken, User

__all__ = [
    "ENUM_TYPES",
    "AssetKind",
    "AssetStatus",
    "Base",
    "CreditBucket",
    "CreditLedgerEntry",
    "Job",
    "JobFamily",
    "JobStatus",
    "JobTool",
    "LedgerReason",
    "MediaAsset",
    "Payment",
    "PaymentKind",
    "PaymentProvider",
    "PaymentStatus",
    "Plan",
    "PlanCode",
    "Project",
    "ProjectAsset",
    "ProviderEvent",
    "RefreshToken",
    "SoftDeleteMixin",
    "SubStatus",
    "Subscription",
    "TimestampMixin",
    "UUIDPrimaryKey",
    "User",
    "UserStatus",
    "WatermarkMode",
    "enum_labels",
]
