# M6 — money, and the five things that were wrong

**Built 31 August 2026**, against [`20-m6-readiness.md`](20-m6-readiness.md) and
in the order that note suggested. Everything in the M6 checklist ships except
two items that are not code and one paragraph of thought that is still owed
(§6).

This note is the part worth reading afterwards: **what the readiness note got
right, what it missed, and the five defects that only appeared once something
was running** — three the tests found, and two they could not have. The feature list is in
[`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) and there is no value in repeating it
here.

| | |
|---|---|
| Backend tests | **315 → 444** |
| Frontend tests | **319 → 352** |
| Migrations | `0005` … `0008` |
| Contract | §7 amended in three places, all marked ⚠️ |

---

## 1. The four traps were real, and one of them had already fired

The readiness note named four. Three behaved exactly as predicted and were
closed as written. The fourth turned out to have a sibling nobody had looked
for.

**`plans.is_public`** now has two readers, not one. `GET /plans` filters on it —
that was the item — but so does `POST /billing/checkout`, because hiding a plan
from the list while still selling it leaves a bookmarked pricing page selling a
retired plan. Retiring `beta` is still one boolean; it now retires it in both
places.

**`queue_priority`** is 0. Rather than assert that one value, the test asserts
that **every** plan's band is one of Celery's `priority_steps` — the trap is
about plan six, not plan five.

**The two dictionaries in `plans.py`** have their `beta` entries. They also have
a guard: both accessors now raise a message naming the file to edit, and
`tests/test_plans.py` walks `PlanCode` and asserts coverage of both tables. The
`KeyError` on the claim path cannot come back silently.

**The renewal sweep** never touches `topup`, and this is the one place the note
asked for the test to be written first. It was — and then verified the only way
that means anything: by widening the sweep to all three buckets and watching
five tests fail with the balance the widening destroyed.

### The trap nobody had listed

`_plan_for_height` in `services/jobs.py` was three `if` statements deciding
which plan to name in a `PLAN_LIMIT_EXCEEDED` error. With five tiers it was
wrong twice over:

* it sent a free user wanting 1080p to **Pro at $19.99** for something `beta`
  covers at **$3.99**;
* it would have kept naming `beta` after `is_public` retired it.

Both failures are silent — the message stays grammatical, it is just wrong, and
the wrong version costs the customer five times more. It now reads the plans
table, filtered on `is_public`, ordered by price. Which makes it the *third*
reader of that column, and the reason the retirement will actually work.

---

## 2. Five defects, and where each of them was hiding

### 2.1 A downgrade that was announced and never written

`POST /billing/checkout` with a cheaper plan scheduled the change and then
**raised** a 409 to report it. `get_session` rolls back on any exception, so the
response said *"your downgrade is scheduled for the end of the period"* and
nothing was scheduled.

The same trap [`auth.py`](../backend/app/api/routes/auth.py) documents on its
refresh-token-reuse branch, in a codebase that had already written it up once.
Found by a test that asserted **the row** rather than the status code — the
version asserting only the response passed against code that did nothing.

The fix was not a commit-before-raise. A scheduled downgrade is a *success*, so
it returns `200` with `checkoutUrl: null`, `scheduledPlan` and `effectiveAt`,
and the state change sits on the ordinary path where a commit happens anyway.
Contract §7 amended.

### 2.2 A rejected row that poisoned the next transaction

Two places caught an `IntegrityError` from a unique index — the commission
accrual, and the webhook's `provider_events` insert — and both did it like this:

```python
session.add(row)
try:
    async with session.begin_nested():
        await session.flush()
except IntegrityError:
    ...                        # correct, and logged correctly
```

**`add` outside the savepoint.** The savepoint rollback does not discard an
object added before it: the row stays pending, and the *next* flush retries the
same doomed INSERT.

On the commission path a test caught it immediately. On the webhook route it was
worse and invisible: the duplicate is answered `200`, and then the commit
`get_session` performs at the end of the request raises — so **a redelivery
returns `500`**, the provider reads that as a failure, retries, and every retry
`500`s again. A loop that ends when somebody notices.

🔴 **The suite structurally could not see it.** `conftest`'s `get_session`
override wraps every request in a savepoint so the test can be rolled back;
production wraps it in nothing, and the savepoint absorbs exactly this failure.
Three successive attempts at a regression test passed against the broken code
before that was understood. The test that finally bites builds a
production-shaped session, commits for real, and cleans up by hand — and it was
confirmed by reverting the fix and watching it fail with the production error.

*This is worth generalising: any request-scoped transaction behaviour is
invisible to this suite.* Nothing else currently depends on one, and the next
thing that does needs the same treatment.

### 2.3 A lazy load after a flush

Saving a template over an existing name returned `500`. `updated_at` carries an
`onupdate`, so the flush expires it and the attribute is re-read when the
response is serialised — outside the greenlet asyncpg needs. One
`await session.refresh(row)`. Ordinary, and it was on the ordinary path: saving
over a template you already have.

### 2.4 And two the tests could not have found

Both appeared in ninety seconds of driving the real pages, and neither would ever
have failed an assertion.

**Every credit movement was listed twice.** The ledger appended each page to what
it already held — including the first — and React 18 runs an effect twice on
mount. The first page now *replaces* and later pages append, which is also the
right shape for the case that has nothing to do with development mode: anything
that re-runs the initial load.

**A date in French inside an English sentence.** `toLocaleDateString` with no
locale respects the viewer's, which is the courteous default and the wrong one
here — nothing else in the product is translated, so *"expires 6 octobre 2026"*
reads as a bug rather than as a kindness. Pinned to `en-GB`, which also avoids the
03/04 ambiguity a numeric format would carry.

The same lesson [`12-m4-5-interface-pass.md`](12-m4-5-interface-pass.md) recorded
under *"found by looking, not by testing"*, and it cost the same ninety seconds to
find again.

---

## 3. What was designed rather than decided

Three questions the documents did not answer, decided here and reversible.

**Where the suggested currency comes from.** Contract §7 says "the caller's IP".
There is no GeoIP database in this deployment and there should not be: one that
must be refreshed monthly to keep a *suggestion* accurate is a maintenance
burden out of proportion to what it buys. The country is read from the edge
network's own header (`CF-IPCountry` and three equivalents), `XX` and `T1` are
discarded as "unknown" and "Tor", and behind no CDN it falls back to USD. The
client offers the override regardless, which is what §7 actually cares about.

**Who processes dollars.** §8.2 says Stripe. Stripe is deferred, so
`BILLING_PROVIDER_FOR_USD` sends them to Razorpay — which is what "Razorpay
first" means in practice — and switching on the day Stripe lands is that one
variable. Whether Razorpay *may* charge USD is still §5's open question.

**`approxVideosPerMonth`.** The two documents that mention it imply different
reference projects: contract §7's example works out at ~100 credits a video,
[`13-mvp-direction.md`](13-mvp-direction.md) §3 at ~27. It is derived from
`pricing.py` against a stated reference — ten minutes through all four phase-1
tools, 60 credits — and rounded **down**, which is the conservative reading. It
moves when prices move, and it is marketing: credits stay the unit.

🟠 **Two placeholders are marked as such in the code and need a decision before
launch**: the top-up pack prices (no document states them; they are derived from
the plan prices and set slightly above a subscription credit, because a top-up
cheaper per credit than a subscription is a reason not to subscribe), and the
storage quotas that have carried a `PLACEHOLDER` marker since M2.

---

## 4. The number the plan depends on

`SECONDS_PER_MINUTE_OF_MEDIA` was a heuristic. It still is — but it is now
**checkable**, which was the actual ask.

`jobs.media_duration_ms` records what each job chewed through, written at
pricing time because that is when the number is already known. `make job-costs`
reports measured seconds-per-minute per tool against what `pricing.py` assumes,
plus the worst case per plan: the whole allowance through the costliest tool per
credit, which is the mix a heavy user finds without trying.

Three decisions inside it are worth knowing:

* **the median, not the mean.** One job that paid for a cold model download
  would drag a mean past every other job in the sample combined;
* **fewer than five samples is not a measurement**, and is labelled rather than
  reported;
* **jobs from before the column are not backfilled.** A reconstructed duration
  would sit in this report looking exactly like a measurement, and this report
  exists to be trusted about one number.

It **reports and does not adjust** — the same rule the ledger reconciliation
follows. A drift of 1.25× is a pricing conversation, not a patch to `pricing.py`
by whoever ran the script.

---

## 5. 🔴 What is still outside this repository

Unchanged from the readiness note, because neither can be closed from here.

**The webhook secret does not exist.** It is issued when a webhook endpoint is
created in the Razorpay dashboard. Everything on our side is written and tested
against a secret of our own choosing — including that an unconfigured verifier
**fails closed**, which is the case that matters, because a check returning
quietly when it has no secret is indistinguishable from a working one. What has
not happened is a real delivery. Until it does, the honest statement is: the
signature check is correct as specified, and has never met Razorpay.

**Whether the account may charge USD** is an activation matter and not an API
capability. `make razorpay-check ARGS=--currency` narrows it; only going live
settles it.

---

## 6. Still owed, and by whom

🔴 **The commission payout process** — schedule, threshold, channel, tax. Accrual
ships and is exact; paying it is a commercial process with no owner, and it is
needed by the tenth server owner rather than the first
([`13-mvp-direction.md`](13-mvp-direction.md) §6).

⚠️ **One paragraph on referral abuse** before launch rather than after:
self-referral, codes shared outside the server, and a chargeback landing after a
commission is paid. `CommissionReason.REVERSAL` exists for the third and nothing
writes one yet — a reversal is a decision somebody makes, not something to
automate before anybody has made it once.

🟠 **The pack prices and the storage quotas** need a sign-off. Both are marked in
the code; neither is a development decision.

---

## 7. What M7 inherits from this

The security milestone gets a wider surface than it had, and the additions are
in the two categories it cares about most.

**New public input.** A promo code is typed by a stranger into a sign-up form
and looked up in the database. It is length-bounded, matched on a primary key
rather than a `LIKE`, and returns the same shape for a code that exists and one
that does not — but it is a new unauthenticated path to a table, and it should
be read as one.

**New money paths.** Two unauthenticated webhook routes whose entire defence is
a signature; an open-redirect check on `returnUrl` that is origin-exact rather
than a suffix match; and a commission ledger somebody has a financial interest
in inflating.

**A new stored blob.** `templates.settings` is JSON the server does not
interpret. It is size-bounded and never executed, and it is rendered by the
editor — which is where anything stored and rendered deserves a second look.

---

## 8. Where to read next

| Read this | Before |
|---|---|
| [`services/credits.py`](../backend/app/services/credits.py) | Touching anything that moves credits — including the new `roll_period` |
| [`services/billing/service.py`](../backend/app/services/billing/service.py) | Any change to grants, renewals or cancellation |
| [`services/billing/providers/base.py`](../backend/app/services/billing/providers/base.py) | Adding Stripe. The interface was designed for two |
| [`tests/test_billing_renewal.py`](../backend/tests/test_billing_renewal.py) | Editing the sweep. The first test is the one whose failure is a refund |
| §2.2 above | Writing any `except IntegrityError` around a savepoint |

---

*Build note · 31 August 2026 · M6 complete but for two things that are not code, and one paragraph that is not written*
