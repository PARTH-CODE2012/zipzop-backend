"""Promo codes: one per Discord server owner.

**The sign-up field is the visible tenth of this.** The hard part is that the
attribution has to outlive the session — the commission is owed on a
subscription that may not happen for months, so a code held anywhere but on the
user row is a commission that can never be paid
(docs/13-mvp-direction.md §6).

The code grants **bonus credits, not a discount**. A discount plus a 15%
commission on the same $3.99 leaves almost nothing, and it takes it from the one
number that has to cover transcription, trimming, storage and export. Bonus
credits cost variable compute, which we control and can retune without touching
a published price.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models import (
    CommissionLedgerEntry,
    CommissionReason,
    PromoCode,
    Subscription,
    SubStatus,
    User,
)
from app.models.enums import PlanCode
from app.services.credits import CreditLedger

log = get_logger(__name__)

#: Longest code we will look up. Not a validation rule so much as a refusal to
#: run a database query for a megabyte of text somebody pasted into the field.
MAX_CODE_LENGTH = 64

#: Providers where dots in the local part are ignored — `a.b@gmail.com` and
#: `ab@gmail.com` are one inbox.
_DOTLESS_DOMAINS = {"gmail.com": "gmail.com", "googlemail.com": "gmail.com"}


def mailbox(email: str) -> str:
    """The inbox an address delivers to, as near as can be told from the text.

    `+tags` dropped everywhere, dots dropped where the provider ignores them.
    Used for one thing: telling that a code's owner and the account using it
    are the same person (M7-23) — an owner signing up `me+alt@gmail.com` with
    their own code to collect the bonus, or to earn commission on themselves.
    """
    local, _, domain = email.strip().lower().rpartition("@")
    local = local.split("+", 1)[0]
    if domain in _DOTLESS_DOMAINS:
        local = local.replace(".", "")
        domain = _DOTLESS_DOMAINS[domain]
    return f"{local}@{domain}"


async def is_self_referral(session: AsyncSession, *, code: PromoCode, user: User) -> bool:
    """Is `user` the owner of `code`, by account or by inbox?

    The evident cases only, on purpose (M7-23's default policy): the owner's own
    account, or an address that delivers to the owner's inbox. A second inbox
    the owner controls is indistinguishable from a friend, and refusing on a
    weaker signal — the same IP, a shared household — would refuse genuine
    referrals the commission exists to reward. The hold and the reversal on
    refund (`billing.service`) are what limit the rest.
    """
    if code.owner_user_id is None:
        return False
    if code.owner_user_id == user.id:
        return True
    owner = await session.get(User, code.owner_user_id)
    return owner is not None and mailbox(owner.email) == mailbox(user.email)


async def find(session: AsyncSession, code: str | None) -> PromoCode | None:
    """An active code, or nothing.

    An inactive code resolves to `None` here on purpose: a retired code must
    stop granting bonuses and stop earning commission, while the rows that
    already point at it keep resolving — which is why it is deactivated rather
    than deleted.
    """
    if not code:
        return None
    cleaned = code.strip()
    if not cleaned or len(cleaned) > MAX_CODE_LENGTH:
        return None
    found = await session.get(PromoCode, cleaned)
    return found if found is not None and found.is_active else None


async def attach_at_signup(session: AsyncSession, *, user: User, code: str | None) -> int:
    """Record the attribution and grant the bonus. Returns the credits granted.

    **Written once, at registration, and never revisited.** There is deliberately
    no way to add a code to an existing account: an attribution that can be set
    later is an attribution that can be set by whoever asks loudest after the
    fact, and it would let one owner claim a customer another one brought in.

    An unknown or retired code is not an error. Registration must not fail
    because somebody mistyped a word from a chat message — the account is
    created, the field is left empty, and the sign-up form's own check
    (`GET /promo/{code}`) is what tells them beforehand.
    """
    promo = await find(session, code)
    if promo is None:
        if code:
            log.info("promo_code_not_applied", code=code[:MAX_CODE_LENGTH], reason="unknown")
        return 0

    if await is_self_referral(session, code=promo, user=user):
        # The owner's own inbox. No bonus, and no attribution — which is also
        # what stops every later payment on this account earning commission.
        # Not an error either: registration goes ahead, as for a mistyped code.
        log.info("promo_code_not_applied", code=promo.code, reason="self_referral")
        return 0

    user.promo_code = promo.code
    user.promo_code_applied_at = datetime.now(UTC)

    if promo.bonus_credits > 0:
        await CreditLedger(session).grant_promo_bonus(
            user=user, credits=promo.bonus_credits, code=promo.code
        )
    await session.flush()
    log.info("promo_code_applied", code=promo.code, user_id=str(user.id))
    return promo.bonus_credits


@dataclass(frozen=True, slots=True)
class OwnerStats:
    """What a server owner is owed, and what brought it in.

    Per currency, and never summed across them: a code can bring in rupee and
    dollar subscribers, and one number covering both would be invented.
    """

    code: str
    is_active: bool
    signups: int
    subscribers: int
    accrued_minor: dict[str, int] = field(default_factory=dict)
    paid_minor: dict[str, int] = field(default_factory=dict)
    owed_minor: dict[str, int] = field(default_factory=dict)
    #: Owed, but earned on payments younger than the hold — still exposed to a
    #: refund or a chargeback (M7-23).
    pending_minor: dict[str, int] = field(default_factory=dict)
    #: What may be paid out today: owed, less what is pending, never below zero.
    payable_minor: dict[str, int] = field(default_factory=dict)


async def owner_stats(
    session: AsyncSession, *, code: str, now: datetime | None = None
) -> OwnerStats | None:
    promo = await session.get(PromoCode, code)
    if promo is None:
        return None

    signups = await session.scalar(
        sa.select(sa.func.count()).select_from(User).where(User.promo_code == promo.code)
    )

    # A subscriber is someone this code brought in who is on a paid plan right
    # now — not someone who ever was. It is the number an owner would count
    # themselves, and overstating it would be the first thing they notice.
    subscribers = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Subscription)
        .join(User, User.id == Subscription.user_id)
        .where(
            User.promo_code == promo.code,
            Subscription.plan != PlanCode.FREE,
            Subscription.status.in_([SubStatus.ACTIVE, SubStatus.PAST_DUE]),
        )
    )

    rows = await session.execute(
        sa.select(
            CommissionLedgerEntry.currency,
            CommissionLedgerEntry.reason,
            sa.func.sum(CommissionLedgerEntry.amount_minor),
        )
        .where(CommissionLedgerEntry.code == promo.code)
        .group_by(CommissionLedgerEntry.currency, CommissionLedgerEntry.reason)
    )

    accrued: dict[str, int] = {}
    paid: dict[str, int] = {}
    owed: dict[str, int] = {}
    for currency, reason, total in rows.all():
        amount = int(total or 0)
        # Every row is signed, so what is owed is simply the sum: an accrual
        # adds, a payout and a reversal subtract. No status to keep in step.
        owed[currency] = owed.get(currency, 0) + amount
        if reason is CommissionReason.ACCRUAL:
            accrued[currency] = accrued.get(currency, 0) + amount
        elif reason is CommissionReason.PAYOUT:
            paid[currency] = paid.get(currency, 0) - amount

    # The hold (M7-23): commission on a payment younger than
    # `commission_hold_days` is owed but not yet payable, net of any reversal
    # that has already landed on that payment. Computed per payment rather than
    # per row so a refund inside the window cancels its own accrual, and does
    # not also come off what was already payable.
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.commission_hold_days)
    recent_payments = (
        sa.select(CommissionLedgerEntry.payment_id)
        .where(
            CommissionLedgerEntry.code == promo.code,
            CommissionLedgerEntry.reason == CommissionReason.ACCRUAL,
            CommissionLedgerEntry.created_at > cutoff,
        )
        .scalar_subquery()
    )
    held_rows = await session.execute(
        sa.select(CommissionLedgerEntry.currency, sa.func.sum(CommissionLedgerEntry.amount_minor))
        .where(
            CommissionLedgerEntry.code == promo.code,
            CommissionLedgerEntry.reason.in_([CommissionReason.ACCRUAL, CommissionReason.REVERSAL]),
            CommissionLedgerEntry.payment_id.in_(recent_payments),
        )
        .group_by(CommissionLedgerEntry.currency)
    )
    pending = {currency: int(total or 0) for currency, total in held_rows.all()}
    payable = {
        currency: max(0, total - pending.get(currency, 0)) for currency, total in owed.items()
    }

    return OwnerStats(
        code=promo.code,
        is_active=promo.is_active,
        signups=int(signups or 0),
        subscribers=int(subscribers or 0),
        accrued_minor=accrued,
        paid_minor=paid,
        owed_minor=owed,
        pending_minor=pending,
        payable_minor=payable,
    )
