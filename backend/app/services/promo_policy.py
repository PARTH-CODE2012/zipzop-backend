"""The referral rules, enforced: the project lead's graduated policy.

Decided on 6 October 2026, in answer to docs/24-m7-closure.md §7 item 4:

* a **violation** is a code's owner using their own code, or sharing it outside
  their Discord server;
* the **first** cancels the commission it earned and is a warning;
* the **second** removes the code.

Sharing a code outside a server is not something the API can see, so a person
judges whether a violation happened. This module is the bookkeeping that
follows that decision, run from `app.scripts.promo_violation`. The automatic
half of the self-use rule already lives at sign-up and on every accrual
(`promo.is_self_referral`). What lands here is what got past it: a second inbox
the owner controls, a code posted in a public channel.

A separate module from `promo` and `billing.service` because it needs both, and
`billing.service` already imports `promo`.
"""

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import (
    CommissionLedgerEntry,
    CommissionReason,
    Payment,
    PromoCode,
    PromoViolation,
    PromoViolationAction,
    PromoViolationKind,
    User,
)
from app.services.billing import service as billing

log = get_logger(__name__)

#: The violation that removes the code. The lead's rule: the second.
REMOVE_CODE_AT = 2


@dataclass(frozen=True, slots=True)
class ViolationOutcome:
    code: str
    #: 1 for this code's first violation, 2 for its second, and so on.
    number: int
    action: PromoViolationAction
    #: Commission taken back, per currency, positive. Never summed across
    #: currencies, for the reason `promo.OwnerStats` gives.
    reversed_minor: dict[str, int] = field(default_factory=dict)
    #: Whether the referred account was detached from the code, which is what
    #: stops its *later* renewals from earning the commission again.
    attribution_removed: bool = False


async def record_violation(
    session: AsyncSession,
    *,
    code: str,
    kind: PromoViolationKind,
    referred_user: User | None = None,
    note: str | None = None,
) -> ViolationOutcome:
    """Record one violation against `code` and apply the policy. Flushes; the
    caller commits.

    **What "cancel that commission" does, concretely.** For the account that
    came in through the breach (`referred_user`):

    * every accrual that account earned for this code and that has not already
      been reversed gets a `REVERSAL` row. It is a row, not an edit, exactly as
      a refund is (`billing.service.reverse_commission`), so the history says
      what happened and why;
    * the account is detached from the code. Otherwise its next renewal would
      accrue the same commission again, and the violation would keep paying.
      This is the one reading of the lead's rule that goes beyond its words.
      It is recorded in docs/24-m7-closure.md §2.6 for them to confirm.

    The bonus credits granted at sign-up are left alone: the policy is about
    the owner's commission, not the customer's account.

    A violation with no `referred_user` (a code seen posted in public before
    anyone used it) cancels nothing, but it still counts towards the second.
    """
    promo_row = await session.scalar(
        sa.select(PromoCode).where(PromoCode.code == code).with_for_update()
    )
    if promo_row is None:
        raise LookupError(f"no promo code {code!r}")

    # Counted under the row lock, so two people recording at once cannot both
    # see "this is the first".
    previous = await session.scalar(
        sa.select(sa.func.count())
        .select_from(PromoViolation)
        .where(PromoViolation.code == promo_row.code)
    )
    number = int(previous or 0) + 1
    action = (
        PromoViolationAction.CODE_REMOVED
        if number >= REMOVE_CODE_AT
        else PromoViolationAction.WARNING
    )
    why = f"referral violation #{number}: {kind.value}"

    reversed_minor: dict[str, int] = {}
    attribution_removed = False
    if referred_user is not None:
        reversed_minor = await _reverse_earned_by(
            session, code=promo_row.code, user=referred_user, why=why
        )
        if referred_user.promo_code is not None and (
            referred_user.promo_code.lower() == promo_row.code.lower()
        ):
            referred_user.promo_code = None
            referred_user.promo_code_applied_at = None
            attribution_removed = True

    if action is PromoViolationAction.CODE_REMOVED:
        # Retired, not deleted, as any code is: the rows pointing at it keep
        # resolving and what was legitimately earned stays payable. A retired
        # code grants nothing at sign-up (`promo.find`) and accrues nothing
        # (`accrue_commission`), which is what "remove the code" has to mean.
        promo_row.is_active = False

    session.add(
        PromoViolation(
            code=promo_row.code,
            kind=kind.value,
            referred_user_id=referred_user.id if referred_user is not None else None,
            action=action.value,
            note=note,
        )
    )
    await session.flush()

    log.info(
        "promo_violation_recorded",
        code=promo_row.code,
        number=number,
        kind=kind.value,
        action=action.value,
        referred_user_id=str(referred_user.id) if referred_user is not None else None,
        reversed_minor=reversed_minor,
        attribution_removed=attribution_removed,
    )
    return ViolationOutcome(
        code=promo_row.code,
        number=number,
        action=action,
        reversed_minor=reversed_minor,
        attribution_removed=attribution_removed,
    )


async def _reverse_earned_by(
    session: AsyncSession, *, code: str, user: User, why: str
) -> dict[str, int]:
    """Reverse every not-yet-reversed accrual `user` earned for `code`."""
    accruals = (
        (
            await session.execute(
                sa.select(CommissionLedgerEntry).where(
                    CommissionLedgerEntry.code == code,
                    CommissionLedgerEntry.user_id == user.id,
                    CommissionLedgerEntry.reason == CommissionReason.ACCRUAL,
                )
            )
        )
        .scalars()
        .all()
    )

    reversed_minor: dict[str, int] = {}
    for accrual in accruals:
        if accrual.payment_id is not None:
            payment = await session.get(Payment, accrual.payment_id)
            if payment is None:  # pragma: no cover - the FK nulls the column first
                continue
            # The refund path's own function: one reversal per payment, guarded
            # by the unique index, so a payment already refunded or charged back
            # is not reversed twice.
            amount = await billing.reverse_commission(session, payment=payment, why=why)
        else:
            amount = await _reverse_unlinked(session, accrual=accrual, why=why)
        if amount:
            reversed_minor[accrual.currency] = reversed_minor.get(accrual.currency, 0) + amount
    return reversed_minor


async def _reverse_unlinked(
    session: AsyncSession, *, accrual: CommissionLedgerEntry, why: str
) -> int:
    """An accrual whose payment row is gone (`ON DELETE SET NULL`).

    The unique index cannot guard these, since it only covers rows with a
    payment. A second violation against the same account must still not take
    the money back twice, so this nets what was already reversed for this
    account and currency on payment-less rows first.
    """
    already = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(CommissionLedgerEntry.amount_minor), 0)).where(
            CommissionLedgerEntry.code == accrual.code,
            CommissionLedgerEntry.user_id == accrual.user_id,
            CommissionLedgerEntry.currency == accrual.currency,
            CommissionLedgerEntry.payment_id.is_(None),
            CommissionLedgerEntry.reason.in_([CommissionReason.ACCRUAL, CommissionReason.REVERSAL]),
        )
    )
    outstanding = int(already or 0)
    if outstanding <= 0:
        return 0
    amount = min(outstanding, accrual.amount_minor)
    session.add(
        CommissionLedgerEntry(
            code=accrual.code,
            user_id=accrual.user_id,
            payment_id=None,
            reason=CommissionReason.REVERSAL,
            amount_minor=-amount,
            currency=accrual.currency,
            rate_bps=accrual.rate_bps,
            note=why,
        )
    )
    await session.flush()
    return amount
