"""Request and response shapes for /auth and /me — contract §2."""

from datetime import datetime

from pydantic import EmailStr, Field

from app.api.schemas.common import ApiModel

#: Long enough to resist a guess, with no upper bound that matters: the hash is
#: SHA-256'd before bcrypt sees it (app/services/security.py), so a passphrase
#: is not truncated and not rejected.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


class RegisterRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(ApiModel):
    email: EmailStr
    # No length bounds on login. Enforcing them here would reject an old
    # password with a *different* error than a wrong one, which is a slower way
    # of doing exactly what the identical-error rule forbids.
    password: str


class UserSummary(ApiModel):
    id: str
    email: str
    display_name: str | None = None


class SessionResponse(ApiModel):
    """What register and login return.

    **No `refreshToken` field.** The contract's version 1.1 put it in the body;
    it is now delivered as an httpOnly cookie instead, so no script can read
    it and an XSS cannot walk away with a 30-day session. The change is
    recorded in docs/05-api-contract.md §2.
    """

    user: UserSummary
    access_token: str
    expires_in: int


class RefreshResponse(ApiModel):
    access_token: str
    expires_in: int


class PlanLimits(ApiModel):
    """A copy of the plan's row, sent so the client can grey out what it cannot
    do. The server enforces the same limits regardless — this is presentation
    only (contract §2)."""

    max_export_height: int
    watermark: str
    monthly_credits: int
    facemap_seconds: int
    concurrent_jobs: dict[str, int]


class SubscriptionSummary(ApiModel):
    plan: str
    display_name: str
    status: str
    currency: str | None = None
    current_period_end: datetime
    cancel_at_period_end: bool
    provider: str | None = None
    limits: PlanLimits


class CreditBalances(ApiModel):
    """Three balances, not one.

    `plan` expires at `planCreditsExpireAt`; `topup` never does;
    `facemapSeconds` is a separate meter only face mapping and lip sync can
    spend. `total` is what the user can spend right now and is the number to
    show most prominently.
    """

    plan: int
    topup: int
    total: int
    facemap_seconds: int
    plan_credits_expire_at: datetime | None = None


class MeResponse(ApiModel):
    id: str
    email: str
    display_name: str | None = None
    storage_bytes_used: int
    created_at: datetime
    credits: CreditBalances
    subscription: SubscriptionSummary | None = None
