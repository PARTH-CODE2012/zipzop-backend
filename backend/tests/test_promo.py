"""Promo codes, attribution and commission — docs/13-mvp-direction.md §6.

Three properties, and only the first is visible to the person typing the code:

* the bonus is granted, **into the bucket that does not expire** — a gift that
  vanishes at the end of the first month is worse than no gift;
* the attribution is written **once, permanently**, because the commission is
  owed on a subscription that may not happen for months;
* an accrual **cannot happen twice for one payment**, which is the database's
  job and not ours.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CommissionLedgerEntry,
    CommissionReason,
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    PlanCode,
    PromoCode,
    Subscription,
    SubStatus,
    User,
)
from app.services import promo
from app.services.billing import service
from app.services.billing.providers.razorpay import RazorpayProvider

V1 = "/v1"


async def _code(
    db: AsyncSession,
    *,
    code: str | None = None,
    bonus: int = 300,
    bps: int = 1500,
    active: bool = True,
    owner: User | None = None,
) -> PromoCode:
    row = PromoCode(
        code=code or f"srv{uuid.uuid4().hex[:8]}",
        owner_label="A Discord server owner",
        owner_user_id=owner.id if owner else None,
        bonus_credits=bonus,
        commission_bps=bps,
        is_active=active,
    )
    db.add(row)
    await db.flush()
    return row


async def _register(client: AsyncClient, **extra: Any) -> dict[str, Any]:
    response = await client.post(
        f"{V1}/auth/register",
        json={
            "email": f"{uuid.uuid4().hex[:12]}@example.com",
            "password": "hunter2hunter2",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------
# The bonus
# --------------------------------------------------------------------------


async def test_a_code_grants_its_bonus_at_signup(client: AsyncClient, db: AsyncSession) -> None:
    row = await _code(db, bonus=300)
    await db.commit()

    body = await _register(client, promoCode=row.code)
    user_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))

    user = await db.get(User, user_id)
    assert user is not None
    assert user.promo_code == row.code
    assert user.promo_code_applied_at is not None
    assert user.topup_credits == 300


async def test_the_bonus_lands_where_it_cannot_expire(
    client: AsyncClient, db: AsyncSession
) -> None:
    """🔴 `topup`, not `plan`.

    The `plan` bucket is swept at every period boundary, so a bonus granted
    there would silently vanish at the end of the user's first month — a gift
    that expires before it is noticed, in a channel where that gets posted back
    to the Discord server the code came from.
    """
    row = await _code(db, bonus=300)
    await db.commit()

    body = await _register(client, promoCode=row.code)
    user_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))

    entry = (
        await db.execute(
            sa.select(CreditLedgerEntry).where(
                CreditLedgerEntry.user_id == user_id,
                CreditLedgerEntry.reason == LedgerReason.PROMO_GRANT,
            )
        )
    ).scalar_one()
    assert entry.bucket is CreditBucket.TOPUP
    assert entry.delta == 300
    assert row.code in (entry.note or "")


async def test_the_bonus_has_its_own_reason(client: AsyncClient, db: AsyncSession) -> None:
    """Not `signup_grant`. The ledger is what a support conversation about
    "where did my credits come from" is answered from, and two rows both saying
    `signup_grant` cannot answer it."""
    row = await _code(db)
    await db.commit()

    body = await _register(client, promoCode=row.code)
    user_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))

    reasons = (
        (
            await db.execute(
                sa.select(CreditLedgerEntry.reason)
                .where(CreditLedgerEntry.user_id == user_id)
                .order_by(CreditLedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert reasons == [LedgerReason.SIGNUP_GRANT, LedgerReason.PROMO_GRANT]


async def test_a_code_is_matched_whatever_the_case(client: AsyncClient, db: AsyncSession) -> None:
    """People type these by hand from a chat message. `ZIPZOP` and `zipzop`
    being two different codes would be a support ticket a week."""
    row = await _code(db, code="LaunchDay")
    await db.commit()

    body = await _register(client, promoCode="LAUNCHDAY")
    user_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))

    user = await db.get(User, user_id)
    assert user is not None
    assert user.promo_code == row.code


async def test_a_mistyped_code_does_not_stop_registration(client: AsyncClient) -> None:
    """⚠️ The account is created regardless.

    Failing registration over a word somebody mistyped from a chat message
    would lose the customer entirely, which is worse for the server owner than
    losing the commission.
    """
    body = await _register(client, promoCode="not-a-real-code")
    assert body["user"]["id"].startswith("usr_")


async def test_a_retired_code_grants_nothing(client: AsyncClient, db: AsyncSession) -> None:
    row = await _code(db, active=False)
    await db.commit()

    body = await _register(client, promoCode=row.code)
    user_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))

    user = await db.get(User, user_id)
    assert user is not None
    assert user.promo_code is None
    assert user.topup_credits == 0


async def test_the_signup_form_can_check_a_code_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The attribution is written once and never revisited, so a mistyped code
    discovered afterwards can never be applied. A message under the field costs
    one request; the alternative costs a customer and a commission."""
    row = await _code(db, bonus=300)
    await db.commit()

    good = (await client.get(f"{V1}/promo/{row.code}")).json()
    assert good["valid"] is True
    assert good["bonusCredits"] == 300

    bad = (await client.get(f"{V1}/promo/nope")).json()
    assert bad["valid"] is False


async def test_checking_an_unknown_code_is_still_a_200(client: AsyncClient) -> None:
    """A 404 would let anyone enumerate which codes exist, and the client
    renders the same thing either way."""
    assert (await client.get(f"{V1}/promo/whatever")).status_code == 200


# --------------------------------------------------------------------------
# Commission
# --------------------------------------------------------------------------


async def _paid_subscriber(db: AsyncSession, code: PromoCode, *, amount: int = 399) -> Any:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        promo_code=code.code,
        promo_code_applied_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    started = datetime.now(UTC) - timedelta(days=31)
    db.add(
        Subscription(
            user_id=user.id,
            plan=PlanCode.BETA,
            status=SubStatus.ACTIVE,
            current_period_start=started,
            current_period_end=started + timedelta(days=30),
        )
    )
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id=f"pay_{uuid.uuid4().hex[:10]}",
        kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.SUCCEEDED,
        amount_minor=amount,
        currency="USD",
    )
    db.add(payment)
    await db.flush()
    return user, payment


async def test_a_payment_accrues_commission_at_the_stored_rate(
    db: AsyncSession,
) -> None:
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)

    accrued = await service.accrue_commission(db, user=user, payment=payment)

    assert accrued == 59  # 15% of $3.99, in cents, rounded down
    entry = (
        await db.execute(
            sa.select(CommissionLedgerEntry).where(CommissionLedgerEntry.code == code.code)
        )
    ).scalar_one()
    assert entry.reason is CommissionReason.ACCRUAL
    assert entry.currency == "USD"
    assert entry.rate_bps == 1500, "the rate is kept with the row it was computed at"


async def test_one_payment_accrues_once(db: AsyncSession) -> None:
    """⚠️ Webhooks are redelivered, sometimes for days.

    The guard is a unique index on `(payment_id, reason)` — the same defence the
    credit ledger uses against a double refund, and for the same reason: code
    that relies on remembering to check is code that will one day forget.
    """
    code = await _code(db)
    user, payment = await _paid_subscriber(db, code)

    first = await service.accrue_commission(db, user=user, payment=payment)
    second = await service.accrue_commission(db, user=user, payment=payment)

    assert (first > 0, second) == (True, 0)
    rows = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(CommissionLedgerEntry)
            .where(CommissionLedgerEntry.code == code.code)
        )
    ).scalar()
    assert rows == 1


async def test_a_user_with_no_code_accrues_nothing(db: AsyncSession) -> None:
    code = await _code(db)
    user, payment = await _paid_subscriber(db, code)
    user.promo_code = None
    await db.flush()

    assert await service.accrue_commission(db, user=user, payment=payment) == 0


async def test_a_retired_code_stops_earning(db: AsyncSession) -> None:
    """Deactivating stops the commission and keeps the history — which is why
    a code is retired rather than deleted."""
    code = await _code(db, active=False)
    user, payment = await _paid_subscriber(db, code)

    assert await service.accrue_commission(db, user=user, payment=payment) == 0


async def test_commission_is_recomputed_on_every_renewal(db: AsyncSession) -> None:
    """🔴 Not once at sign-up.

    A commission paid once on a recurring product misaligns the owner's
    incentive from month two: they are rewarded for the sign-up and indifferent
    to whether the customer stays.
    """
    code = await _code(db, bps=1500)
    user, first_payment = await _paid_subscriber(db, code, amount=399)

    second_payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id=f"pay_{uuid.uuid4().hex[:10]}",
        kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.SUCCEEDED,
        amount_minor=399,
        currency="USD",
    )
    db.add(second_payment)
    await db.flush()

    await service.accrue_commission(db, user=user, payment=first_payment)
    await service.accrue_commission(db, user=user, payment=second_payment)

    total = (
        await db.execute(
            sa.select(sa.func.sum(CommissionLedgerEntry.amount_minor)).where(
                CommissionLedgerEntry.code == code.code
            )
        )
    ).scalar()
    assert total == 118


# --------------------------------------------------------------------------
# What the owner sees
# --------------------------------------------------------------------------


async def test_the_owner_sees_what_they_are_owed(db: AsyncSession) -> None:
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=payment)

    stats = await promo.owner_stats(db, code=code.code)

    assert stats is not None
    assert stats.signups == 1
    assert stats.subscribers == 1
    assert stats.owed_minor == {"USD": 59}
    assert stats.accrued_minor == {"USD": 59}


async def test_a_payout_reduces_what_is_owed_without_erasing_it(
    db: AsyncSession,
) -> None:
    """Append-only, like the credit ledger. A payout is a negative row rather
    than a status flipped on the accrual, so what is owed is a SUM and the
    history of how it got there survives."""
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=payment)

    db.add(
        CommissionLedgerEntry(
            code=code.code,
            user_id=user.id,
            payment_id=payment.id,
            reason=CommissionReason.PAYOUT,
            amount_minor=-59,
            currency="USD",
            note="paid by hand, first cohort",
        )
    )
    await db.flush()

    stats = await promo.owner_stats(db, code=code.code)
    assert stats is not None
    assert stats.owed_minor == {"USD": 0}
    assert stats.accrued_minor == {"USD": 59}, "the history survives the payout"
    assert stats.paid_minor == {"USD": 59}


async def test_currencies_are_never_summed_together(db: AsyncSession) -> None:
    """A code can bring in rupee and dollar subscribers. One number covering
    both would be invented."""
    code = await _code(db, bps=1500)
    dollar_user, dollar_payment = await _paid_subscriber(db, code, amount=399)
    rupee_user, rupee_payment = await _paid_subscriber(db, code, amount=19_900)
    rupee_payment.currency = "INR"
    await db.flush()

    await service.accrue_commission(db, user=dollar_user, payment=dollar_payment)
    await service.accrue_commission(db, user=rupee_user, payment=rupee_payment)

    stats = await promo.owner_stats(db, code=code.code)
    assert stats is not None
    assert stats.owed_minor == {"USD": 59, "INR": 2_985}


async def test_only_the_owner_can_read_the_figures(client: AsyncClient, db: AsyncSession) -> None:
    """A code's numbers say how many people from somebody else's Discord server
    signed up and how many pay us. That is commercial information about their
    community, not ours to publish."""
    body = await _register(client)
    owner_id = uuid.UUID(body["user"]["id"].removeprefix("usr_"))
    owner = await db.get(User, owner_id)
    assert owner is not None
    code = await _code(db, owner=owner)
    await db.commit()

    owner_headers = {"Authorization": f"Bearer {body['accessToken']}"}
    stranger = await _register(client)
    stranger_headers = {"Authorization": f"Bearer {stranger['accessToken']}"}

    assert (
        await client.get(f"{V1}/promo/{code.code}/stats", headers=owner_headers)
    ).status_code == 200
    # 404 rather than 403, so the response cannot be used to discover that a
    # code exists.
    assert (
        await client.get(f"{V1}/promo/{code.code}/stats", headers=stranger_headers)
    ).status_code == 404


# --------------------------------------------------------------------------
# M7-23 — the default policy: self-referral, the hold, reversal on refund
# --------------------------------------------------------------------------


def test_one_inbox_is_one_inbox_whatever_the_spelling() -> None:
    assert promo.mailbox("Owner+alt@Gmail.com") == promo.mailbox("o.w.n.e.r@googlemail.com")
    assert promo.mailbox("owner+discord@example.com") == "owner@example.com"
    # Dots only fold where the provider ignores them.
    assert promo.mailbox("o.wner@example.com") != promo.mailbox("owner@example.com")


async def test_an_owner_signing_up_their_own_inbox_gets_no_bonus_and_no_attribution(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The cheapest abuse there is: the owner registers `me+1@gmail.com`,
    `me+2@gmail.com`… with their own code, collecting the bonus each time and
    earning commission on anything those accounts ever pay."""
    owner = User(email="server.owner@gmail.com", hashed_password="not-a-real-hash")
    db.add(owner)
    await db.flush()
    code = await _code(db, owner=owner, bonus=300)

    body = await _register(client, email="serverowner+alt@gmail.com", promoCode=code.code)

    alt = await db.get(User, uuid.UUID(body["user"]["id"].removeprefix("usr_")))
    assert alt is not None
    assert alt.promo_code is None
    assert alt.topup_credits == 0


async def test_an_owner_paying_on_their_own_code_earns_no_commission(db: AsyncSession) -> None:
    """An account that carried the code before it was made the code's owner."""
    code = await _code(db)
    user, payment = await _paid_subscriber(db, code)
    code.owner_user_id = user.id
    await db.flush()

    assert await service.accrue_commission(db, user=user, payment=payment) == 0


async def test_a_genuine_referral_still_earns(db: AsyncSession) -> None:
    """The rule refuses the owner's own inbox and nothing wider."""
    owner = User(email="server.owner@gmail.com", hashed_password="not-a-real-hash")
    db.add(owner)
    await db.flush()
    code = await _code(db, owner=owner)
    user, payment = await _paid_subscriber(db, code)

    assert await service.accrue_commission(db, user=user, payment=payment) == 59


async def test_commission_is_pending_inside_the_hold_and_payable_after_it(
    db: AsyncSession,
) -> None:
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=payment)

    today = await promo.owner_stats(db, code=code.code)
    later = await promo.owner_stats(db, code=code.code, now=datetime.now(UTC) + timedelta(days=31))

    assert today is not None and later is not None
    assert (today.owed_minor, today.pending_minor, today.payable_minor) == (
        {"USD": 59},
        {"USD": 59},
        {"USD": 0},
    )
    assert (later.pending_minor, later.payable_minor) == ({}, {"USD": 59})


def _reversal(payment: Payment, *, event: str = "refund.processed") -> dict[str, Any]:
    """A refund or a lost dispute as Razorpay sends it, naming the payment only
    inside the refund or dispute entity — the shape the parser has to cope with
    when the payment entity is absent."""
    entity = "refund" if event.startswith("refund") else "dispute"
    return {
        "entity": "event",
        "event": event,
        "payload": {
            entity: {
                "entity": {
                    "id": f"{entity[:4]}_{uuid.uuid4().hex[:10]}",
                    "payment_id": payment.provider_payment_id,
                    "amount": payment.amount_minor,
                    "currency": payment.currency,
                }
            }
        },
    }


async def _deliver(db: AsyncSession, body: dict[str, Any]) -> Any:
    """Through exactly the path the worker runs: parse, then apply."""
    event = RazorpayProvider().parse_webhook(raw_body=json.dumps(body).encode(), headers={})
    return await service.apply_event(db, event)


async def _owed(db: AsyncSession, code: PromoCode) -> int:
    total = await db.scalar(
        sa.select(sa.func.sum(CommissionLedgerEntry.amount_minor)).where(
            CommissionLedgerEntry.code == code.code
        )
    )
    return int(total or 0)


@pytest.mark.parametrize("event", ["refund.processed", "payment.dispute.lost"])
async def test_money_that_went_back_takes_its_commission_with_it(
    db: AsyncSession, event: str
) -> None:
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=payment)

    outcome = await _deliver(db, _reversal(payment, event=event))

    assert outcome.action == "payment_reversed"
    assert outcome.details["commission_reversed_minor"] == 59
    assert payment.status is PaymentStatus.REFUNDED
    assert await _owed(db, code) == 0
    reversal = await db.scalar(
        sa.select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.payment_id == payment.id,
            CommissionLedgerEntry.reason == CommissionReason.REVERSAL,
        )
    )
    assert reversal is not None and reversal.note == event, "the accrual stays; a row says why"


async def test_a_payment_is_reversed_once_however_often_it_goes_back(db: AsyncSession) -> None:
    """A redelivered refund, then a dispute lost on the same payment: one
    reversal, because one is the whole commission."""
    code = await _code(db, bps=1500)
    user, payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=payment)

    refund = _reversal(payment)
    await _deliver(db, refund)
    again = await _deliver(db, refund)
    dispute = await _deliver(db, _reversal(payment, event="payment.dispute.lost"))

    assert again.details["commission_reversed_minor"] == 0
    assert dispute.details["commission_reversed_minor"] == 0
    assert await _owed(db, code) == 0


async def test_a_reversal_after_payout_is_netted_from_what_comes_next(db: AsyncSession) -> None:
    """Chargeback after the owner was paid: what is owed goes negative, and
    nothing is payable until later commission covers it."""
    code = await _code(db, bps=1500)
    user, first = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=user, payment=first)
    db.add(
        CommissionLedgerEntry(
            code=code.code,
            user_id=user.id,
            payment_id=first.id,
            reason=CommissionReason.PAYOUT,
            amount_minor=-59,
            currency="USD",
        )
    )
    await db.flush()

    await _deliver(db, _reversal(first, event="payment.dispute.lost"))

    later = await promo.owner_stats(db, code=code.code, now=datetime.now(UTC) + timedelta(days=31))
    assert later is not None
    assert later.owed_minor == {"USD": -59}
    assert later.payable_minor == {"USD": 0}


async def test_a_refund_inside_the_hold_cancels_only_its_own_accrual(db: AsyncSession) -> None:
    """Pending is computed per payment, so a refund on a fresh payment comes off
    what is pending, and not a second time off what was already payable."""
    code = await _code(db, bps=1500)
    old_user, old_payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=old_user, payment=old_payment)
    await db.execute(
        sa.update(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.payment_id == old_payment.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=40))
    )
    new_user, new_payment = await _paid_subscriber(db, code, amount=399)
    await service.accrue_commission(db, user=new_user, payment=new_payment)

    await _deliver(db, _reversal(new_payment))

    stats = await promo.owner_stats(db, code=code.code)
    assert stats is not None
    assert stats.owed_minor == {"USD": 59}
    assert stats.pending_minor == {"USD": 0}
    assert stats.payable_minor == {"USD": 59}


async def test_a_refund_on_a_payment_we_never_recorded_is_loud_and_changes_nothing(
    db: AsyncSession,
) -> None:
    code = await _code(db)
    user, payment = await _paid_subscriber(db, code)
    await service.accrue_commission(db, user=user, payment=payment)
    unknown = Payment(
        user_id=user.id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_never_recorded",
        kind=PaymentKind.SUBSCRIPTION,
        status=PaymentStatus.SUCCEEDED,
        amount_minor=399,
        currency="USD",
    )
    body = _reversal(unknown)
    # With the payment entity Razorpay usually sends alongside, carrying our
    # checkout notes — so the account is known and only the payment is not.
    body["payload"]["payment"] = {
        "entity": {"id": "pay_never_recorded", "notes": {"user_id": str(user.id)}}
    }

    outcome = await _deliver(db, body)

    assert outcome.action == "reversal_unmatched"
    assert await _owed(db, code) == 59
