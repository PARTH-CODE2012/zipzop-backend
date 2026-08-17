"""Accounts, sessions and the credit ledger.

Deliberately **not** a `ScopedRepository`: this is the identity layer, the one
place that legitimately looks a row up by something other than the caller's own
id — because at that point there is no caller yet.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    Plan,
    PlanCode,
    RefreshToken,
    Subscription,
    SubStatus,
    User,
    UserStatus,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- lookups
    async def by_email(self, email: str) -> User | None:
        """CITEXT does the case folding, so no `lower()` here — and none is
        wanted: a functional comparison would not use the unique index."""
        result = await self._session.execute(
            sa.select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            sa.select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        found = await self._session.scalar(
            sa.select(sa.literal(1)).where(
                sa.exists().where(User.email == email, User.deleted_at.is_(None))
            )
        )
        return found is not None

    # -------------------------------------------------------- registration
    async def create_with_free_plan(
        self, *, email: str, hashed_password: str, display_name: str | None
    ) -> User:
        """Create the account, put it on `free`, grant the allowance.

        One transaction, by construction — the caller's session is not
        committed here. contract §2: *"New accounts land on the free plan with
        its monthly allowance already granted."* A user row without its
        subscription, or a balance without its ledger row, is a broken account
        that only a reconciliation job would ever find.
        """
        plan = await self._session.get(Plan, PlanCode.FREE)
        if plan is None:  # pragma: no cover - the seed is part of the migration
            raise RuntimeError("the 'free' plan is missing; migration 0002 seeds it")

        user = User(
            email=email,
            hashed_password=hashed_password,
            display_name=display_name,
            status=UserStatus.ACTIVE,
            plan_credits=plan.monthly_credits,
            facemap_seconds=plan.facemap_seconds,
        )
        self._session.add(user)
        await self._session.flush()

        now = datetime.now(UTC)
        self._session.add(
            Subscription(
                user_id=user.id,
                plan=PlanCode.FREE,
                status=SubStatus.ACTIVE,
                provider=None,  # free tier has no provider
                current_period_start=now,
                current_period_end=_add_a_month(now),
            )
        )

        # One ledger row per bucket that actually moved. `facemap_seconds` is 0
        # on the free plan and the ledger forbids a zero delta, so writing one
        # unconditionally would make every registration fail.
        self._session.add(
            CreditLedgerEntry(
                user_id=user.id,
                bucket=CreditBucket.PLAN,
                delta=plan.monthly_credits,
                reason=LedgerReason.SIGNUP_GRANT,
                balance_after=plan.monthly_credits,
                note="free plan signup allowance",
            )
        )
        if plan.facemap_seconds:
            self._session.add(
                CreditLedgerEntry(
                    user_id=user.id,
                    bucket=CreditBucket.FACEMAP,
                    delta=plan.facemap_seconds,
                    reason=LedgerReason.SIGNUP_GRANT,
                    balance_after=plan.facemap_seconds,
                )
            )

        await self._session.flush()
        return user

    # ------------------------------------------------------- subscriptions
    async def live_subscription(self, user_id: uuid.UUID) -> Subscription | None:
        result = await self._session.execute(
            sa.select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
            )
        )
        return result.scalar_one_or_none()

    async def plan(self, code: PlanCode) -> Plan | None:
        return await self._session.get(Plan, code)


class RefreshTokenRepository:
    """The rotation chain.

    Each use issues a new token and marks the old one replaced. Presenting a
    token that was already replaced revokes the **whole chain** — that pattern
    means the token leaked, and the only safe response is to end every session
    it could have spawned (docs/03-backend-architecture.md §4.2).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """The stolen-token response. Returns how many sessions were ended."""
        result = await self._session.execute(
            sa.update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.flush()
        # An UPDATE always yields a CursorResult; the declared return type of
        # `execute` is the wider `Result`, which has no `rowcount`.
        return cast("CursorResult[Any]", result).rowcount or 0

    async def rotate(
        self,
        *,
        old: RefreshToken,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
    ) -> RefreshToken:
        fresh = await self.issue(
            user_id=old.user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        old.replaced_by = fresh.id
        old.revoked_at = datetime.now(UTC)
        await self._session.flush()
        return fresh


def _add_a_month(moment: datetime) -> datetime:
    """The renewal boundary.

    Calendar months, not 30 days: someone who signs up on the 31st renews on
    the 28th, 30th or 31st as the next month allows, rather than drifting a day
    earlier every month for a year.
    """
    year = moment.year + (moment.month // 12)
    month = moment.month % 12 + 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]
