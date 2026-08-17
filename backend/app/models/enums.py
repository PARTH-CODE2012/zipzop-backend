"""Every PostgreSQL enum type in one place.

Declared as `StrEnum` so a member compares equal to its wire value — a route
can return `asset.status` straight into JSON without a conversion step, and a
test can assert against the literal string from the contract.

`pg_enum()` builds the SQLAlchemy column type. The `values_callable` is not
optional: without it SQLAlchemy stores the member *names* (`PENDING_UPLOAD`)
rather than the values (`pending_upload`), and the database would disagree with
docs/05-api-contract.md on every status string.
"""

import enum

from sqlalchemy import Enum as SAEnum


def pg_enum(python_enum: type[enum.Enum], name: str) -> SAEnum:
    """A native PostgreSQL enum whose labels are the members' values."""
    return SAEnum(
        python_enum,
        name=name,
        native_enum=True,
        create_type=False,  # migrations own type creation, not the model
        values_callable=lambda obj: [str(member.value) for member in obj],
    )


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class AssetKind(enum.StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class AssetStatus(enum.StrEnum):
    """docs/05-api-contract.md §3 exposes the first four; `deleted` is internal.

    An asset is only `ready` once the probe, proxy, thumbnail and peaks all
    exist — see docs/03-backend-architecture.md §6.2.
    """

    PENDING_UPLOAD = "pending_upload"
    PROBING = "probing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class JobFamily(enum.StrEnum):
    ANALYSIS = "analysis"
    RENDER = "render"
    INFERENCE = "inference"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobTool(enum.StrEnum):
    # phase 1
    CAPTIONS = "captions"
    SMART_TRIM = "smart_trim"
    COLOR_ANALYSIS = "color_analysis"
    EXPORT = "export"
    # phase 2
    FACE_MAP = "face_map"
    LIP_SYNC = "lip_sync"
    DENOISE = "denoise"
    DEREVERB = "dereverb"
    # phase 3
    CLIP_FINDER = "clip_finder"
    TEMPLATE_SUGGEST = "template_suggest"
    UPSCALE = "upscale"
    STABILIZE = "stabilize"


class CreditBucket(enum.StrEnum):
    """The three buckets. They expire on different schedules, which is the
    whole reason a balance has to say which credits it holds
    (docs/03-backend-architecture.md §5.4)."""

    PLAN = "plan"  # monthly allowance — expires at period end
    TOPUP = "topup"  # purchased — never expires
    FACEMAP = "facemap"  # face-mapping seconds — expires at period end


class LedgerReason(enum.StrEnum):
    SIGNUP_GRANT = "signup_grant"
    PLAN_GRANT = "plan_grant"
    PLAN_EXPIRY = "plan_expiry"
    TOPUP_PURCHASE = "topup_purchase"
    RESERVE = "reserve"
    REFUND = "refund"
    ADMIN_GRANT = "admin_grant"
    ADMIN_ADJUST = "admin_adjust"


class PlanCode(enum.StrEnum):
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    STUDIO = "studio"


class WatermarkMode(enum.StrEnum):
    FORCED = "forced"
    NONE = "none"
    CUSTOM = "custom"


class SubStatus(enum.StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentProvider(enum.StrEnum):
    STRIPE = "stripe"
    RAZORPAY = "razorpay"


class PaymentKind(enum.StrEnum):
    SUBSCRIPTION = "subscription"
    TOPUP = "topup"


class PaymentStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


#: Every enum type, in the order a migration must create them, paired with the
#: SQL type name. `create_all_types` / `drop_all_types` in the M2 migration walk
#: this so a new enum cannot be added to the application and forgotten in the
#: schema.
ENUM_TYPES: list[tuple[str, type[enum.Enum]]] = [
    ("user_status", UserStatus),
    ("asset_kind", AssetKind),
    ("asset_status", AssetStatus),
    ("job_family", JobFamily),
    ("job_status", JobStatus),
    ("job_tool", JobTool),
    ("credit_bucket", CreditBucket),
    ("ledger_reason", LedgerReason),
    ("plan_code", PlanCode),
    ("watermark_mode", WatermarkMode),
    ("sub_status", SubStatus),
    ("payment_provider", PaymentProvider),
    ("payment_kind", PaymentKind),
    ("payment_status", PaymentStatus),
]


def enum_labels(python_enum: type[enum.Enum]) -> list[str]:
    """The SQL labels for a type, in declaration order."""
    return [str(member.value) for member in python_enum]
