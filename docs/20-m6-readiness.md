# M6 readiness — money, and the four things to get right before writing any of it

**Written 31 August 2026 from a full read of the schema, the credit ledger, the
contract and the config guard — for the developer picking M6 up.** Same purpose
as [`15-m5-readiness.md`](15-m5-readiness.md): establish what is already built
so M6 does not rebuild it, what the contract already decides so it is not
re-decided, and what is genuinely open — with a recommendation rather than a
decision taken without you.

| | |
|---|---|
| **Milestone** | M6 — *"you hit the free limit, subscribe, and the new allowance appears within seconds"* |
| **Status** | 🟢 **Ready to start.** More is built than the checklist suggests — §1 |
| **Blocking** | ⚠️ **Two external things, and neither is code** — §3 |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M6 |
| **Scope grew 25 August** | [`13-mvp-direction.md`](13-mvp-direction.md) — a fifth plan, promo codes and commissions, templates |

> **Read §2 before writing a line.** Money is the one part of this system where
> a bug is not a bad experience but a wrong number in someone's account, and
> four of the traps below are the kind you only find in production.

---

## 1. What M6 inherits, already built and running

This is the shortest list of the five readiness documents, because **the money
model is already in the database and already exercised by every job**.

| | Where | State |
|---|---|---|
| `plans`, `subscriptions`, `payments`, `provider_events` | Migration `0002` | ✅ Migrated **and seeded** with four plans |
| `credit_ledger` — append-only, double-entry | `models/credit.py` | ✅ Written to on every job since M4 |
| Three buckets — `plan` · `topup` · `facemap` | `CreditBucket` | ✅ Reserved from and refunded to, per bucket |
| Reserve · refund · balance-under-lock | `services/credits.py` | ✅ `CreditLedger.reserve`, `.refund`, `.lock_user` |
| Fair-use ceiling | `services/jobs.py` | ✅ Enforced in `quote()`, before affordability |
| Plan gating on export | `services/jobs.py` | ✅ `max_export_height` → `403 PLAN_LIMIT_EXCEEDED` with the upgrade named (M5) |
| Watermark from the plan | `services/render_pipeline.py` | ✅ Read from `plans.watermark`, never from client input (M5) |
| `GET /me` with three balances and a plan | `routes/auth.py` | ✅ Contract v1.1 shape |
| Production refuses test keys | `config.py` | ✅ 8 cases in `test_config.py` |
| Hourly beat schedule for renewal | `workers/celery_app.py` | ✅ Bound; the task is a stub |
| Nightly ledger reconciliation | `services/reconciliation.py` | ✅ **Reports drift, never repairs it** — read its docstring before touching money |

**What is genuinely missing is the outside world**: a provider adapter, the
endpoints in front of it, and the webhook that turns a payment into an
allowance. The accounting underneath is done.

> **The most useful hour you can spend before starting** is reading
> `services/credits.py` and `services/reconciliation.py` end to end. Between them
> they contain every rule about how money moves here, and M6 adds counterparties
> to that model rather than replacing it.

---

## 2. 🔴 The four traps

Each of these is cheap to avoid now and expensive to find later. Three are
already written into the checklist; the fourth is not written anywhere yet.

### 2.1 `plans.is_public` exists and nothing reads it

The column is in migration `0002`. It is **the entire mechanism** for retiring
the `beta` plan once the Discord campaign ends: flip it to `false`, and the plan
vanishes from the pricing table while everyone already on it keeps it.

`GET /plans` must filter on it. An endpoint that ignores the column makes the
retirement a no-op — and you find that out on the day you try to retire the
plan, with customers watching.

### 2.2 `queue_priority` is `0` for `beta`, not `5`

Celery's `priority_steps` are `[0, 10, 20, 30]` and `apply_async(priority=…)`
passes the plan's value through **untranslated**. A value between bands is
silently mapped to a neighbour — no error, no log, just a plan that quietly gets
a queue position nobody chose.

### 2.3 Two dictionaries in `plans.py` will `KeyError` on a new plan

`CONCURRENCY_LIMITS` and `STORAGE_QUOTA_BYTES` are read with a **direct
subscript**:

```python
return dict(CONCURRENCY_LIMITS[plan])   # services/plans.py
return STORAGE_QUOTA_BYTES[plan]
```

A plan missing from either raises on **the claim path and the upload path** —
so adding `beta` to the enum and the table without adding it to both dictionaries
breaks jobs and uploads for exactly the users who just paid.

`test_the_four_plans_are_seeded_with_the_documented_values` in `test_schema.py`
asserts an exact five-key dictionary and **will fail when you add the row. That
is the test working**; update it with the new values.

### 2.4 The renewal sweep must never touch `topup` — and nothing enforces that yet

[`03-backend-architecture.md`](03-backend-architecture.md) §8.4 spells the
transaction out: sweep `plan` and `facemap`, grant the new allowance, leave
`topup` alone, all in one transaction. `topup` credits are **bought** and never
expire, and a renewal that swept them would be taking money already paid.

There is no test for this yet because there is no renewal yet. **Write that test
first** — it is the one whose failure is a refund and an apology rather than a
bug report.

---

## 3. ⚠️ Two external blockers, neither of them code

Both are named in the checklist and both are outside the repository. Neither
stops you starting, and both stop you finishing.

### 3.1 The webhook secret does not exist yet

Razorpay's test key pair arrived 25 August and is in the developer's `.env`. The
**webhook secret is a third secret** and is not issued until a webhook endpoint
is created in the Razorpay dashboard.

Until it exists the signature check cannot be exercised — and **an untested
signature check is indistinguishable from no signature check**, on the one
endpoint where an attacker who gets through grants themselves credits. The
application already refuses to boot in production without it
(`assert_production_safe`), which is a guard, not a substitute.

### 3.2 Confirm the account may charge USD

$3.99 is a dollar price on an Indian processor, and currency availability is an
**account-activation matter, not an API capability**. `make razorpay-check
ARGS=--currency` probes it. Read the result carefully:

* a **refusal in test mode is conclusive** and arrives immediately;
* an **acceptance leaves the question open** until the account goes live, which
  is the answer you cannot act on.

If USD turns out to be unavailable, that is a pricing decision for the project
lead and not a workaround for you to invent.

---

## 4. What the contract already decides

Do not re-litigate these; they are settled in
[`05-api-contract.md`](05-api-contract.md) §7 and worth knowing before you design
anything.

| | |
|---|---|
| **The redirect is never proof of payment** | The subscription activates on the *webhook*. On return the client polls `GET /me` behind a "confirming your payment" state — the user can reach `returnUrl` by pressing back |
| **The provider is derived, never chosen** | INR → Razorpay, everything else → Stripe. Not a client parameter |
| **`suggestedCurrency` is a suggestion** | From IP, and the client must let the user change it. VPNs, travellers and expatriates make IP unreliable |
| **Webhooks: verify → store → 200 → process async** | Store in `provider_events` *before* processing. Duplicates collide on the primary key and are dropped — that is the idempotency, and it is why the table has the shape it has |
| **Cancellation says what it costs, before confirming** | `creditsLostAtPeriodEnd` and `creditsKept` are in the response so the user sees "you will lose 1,840 credits" *at the moment of cancelling*. Fairer, and better retention than finding out a week later |
| **`approxVideosPerMonth` is marketing, credits are the unit** | The figure exists so a pricing page can say something a person understands |
| **`queueLabel` is a word, not a time** | We do not publish an SLA we have not measured |

---

## 5. What the project lead's 30 August notes change

Three points arrived from competitor research (CapCut/InShot/VN reviews). Two of
them are M6's, and both are cheaper than they sound.

**Clear pricing, one-click cancel, no hidden charges.** CapCut carries a 1.2/5
rating on precisely this. Nothing new is needed: `POST /billing/cancel` is
already specified as cancel-at-period-end with the cost stated up front, and
`POST /billing/portal` hands the user the provider's own management page rather
than a rebuilt one. **The work is making sure the interface is as honest as the
contract already is** — no dark patterns in the cancel flow, and the price shown
before the click, the same rule M5's export dialog follows.

**Privacy as a differentiator.** No social feed exists or is planned; the scope
document has no sharing surface. This is not a feature to build, it is **a true
thing to say** — and one claim is stronger than any competitor can match: the
transcription model is **self-hosted** (decided 20 August,
[`11-m4-notes.md`](11-m4-notes.md) §1), so users' speech never leaves our
infrastructure. That is currently written down nowhere a customer can read it.

*(The third note — Hindi caption rendering — was M5's and is done: shaped through
libass/HarfBuzz with a Devanagari font in the image. See
[`19-multipart-and-ci.md`](19-multipart-and-ci.md) and the M5 checklist.)*

---

## 6. The two new blocks, and how much of an unknown each is

### 6.1 The `beta` plan — small, and fully specified

Every value and the reasoning for it is in
[`13-mvp-direction.md`](13-mvp-direction.md) §3. It is a migration, a seed row,
two dictionary entries and a test update. **Watermark `none` is the single most
important value**: removing the watermark is the main reason anyone converts off
free, and a paying user who still sees one churns in the first week.

### 6.2 Discord referrals — the real unknown

`promo`, `referral`, `coupon` and `affiliate` return **zero matches across the
backend**. Nothing exists.

**The sign-up field is the visible tenth of it.** The hard part is that
attribution must outlive the session: the commission is owed on a subscription
that happens *later*, so an attribution lost at sign-up is a commission that can
never be paid. Store it on the user, permanently.

Two design decisions already taken, and worth understanding rather than
rediscovering:

* **The code grants +300 bonus credits, one off — it is not a discount.** A
  discount plus a 15% commission on $3.99 leaves almost nothing; and the free
  tier already gives 300 credits away, so a code that granted nothing would give
  the user no reason to type it and the server owner nothing to announce.
* **Commission accrues as ledger rows, recomputed on every renewal** — not once
  at sign-up. A one-off commission on a recurring product misaligns the owner's
  incentive from month two. Money moving here is already double-entry and
  append-only, so this is **a new counterparty, not a new financial model**.

🔴 **Unowned and needed before the tenth server owner, not the first**: the
actual payout process — schedule, threshold, channel, tax. And one paragraph of
thought about abuse (self-referral, codes shared outside the server, a chargeback
landing after a commission is paid) **before** launch rather than after.

### 6.3 Templates — smaller than it sounds

Decided 25 August as **the user's own settings, saved and reapplied** — caption
style, colour grade, transition defaults, title styling. Not a supplied library:
that reading carries a licensed music library and real-person-naming exposure,
neither of which has an owner.

**No worker, no queue, no credits, no new job type.** It is a subset of the
timeline document, so it belongs beside the editing operations. Apply it as a
**single `commit`**, so it undoes in one step like every other bulk operation.

---

## 7. 🟠 The number that decides whether this plan makes money

`SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is flagged in
[`11-m4-notes.md`](11-m4-notes.md) §8 as **a heuristic, not a measurement**, and
every tier's allowance derives from it.

At $19.99 an error there was absorbed. At $3.99 — net of the 15% commission and
processing, about **$3.28** — it is not. That has to cover a month of
transcription, trimming, storage and export for a user with 800 credits.

**Cost-per-job instrumentation moves from "nice to have on the first deploy" to
the thing that tells you whether the `beta` plan loses money.** It is on the
checklist; treat it as part of shipping the plan rather than as follow-up.

---

## 8. Suggested order

Not prescriptive, but this ordering means each step is testable when you finish
it rather than three steps later.

1. **The `beta` plan** — migration, seed, both dictionaries in `plans.py`, update
   the schema test. Small, self-contained, and it makes §2.2 and §2.3 concrete
   before anything depends on them.
2. **`GET /plans`** — public, filtered on `is_public` (§2.1). No provider needed,
   and it unblocks the pricing page.
3. **The renewal sweep**, with the "never touch `topup`" test written **first**
   (§2.4). No provider needed either: it is the only path free users have, and
   the beat schedule is already bound.
4. **The Razorpay adapter**, against the interface designed for two providers.
5. **Webhooks** — verify, store, 200, process async. Needs §3.1 resolved to be
   testable at all.
6. **Checkout, top-up, portal, cancel.**
7. **`GET /credits/ledger`** — reads a table that has been filling since M4.
8. **The frontend** — pricing page, the confirming state, paywalls that name the
   unblock and link to it. **No dead ends**, and running out mid-project must
   never block plain editing or lose work.
9. **Promo codes and commissions** (§6.2), then **templates** (§6.3).

---

## 9. The state of verification you are inheriting

Honest, because you will be running these.

| | |
|---|---|
| Backend | **315 tests, 2 skipped** — the skips want a warmed `faster-whisper` cache |
| Frontend | **319 tests** |
| Gates | `ruff`, `ruff format`, `mypy app` clean; production build green |
| Infrastructure | Docker Desktop, ffmpeg 9.0.1, Postgres + Redis + MinIO — `make doctor` reports what is missing |
| `make parity` | M5's closing condition, and it needs the web server up |
| 🔴 `make e2e` | **Still never run.** It covers M2 end to end and has been outstanding since M4 |

Two things about this machine that cost hours to rediscover, both written up in
[`17-first-real-test-run.md`](17-first-real-test-run.md):

* `make` itself is not installed in the developer's shell; the `Makefile` targets
  are typically run by hand.
* The `Makefile` resolves `.venv/bin` vs `.venv/Scripts` and tests that `python3`
  actually *executes* rather than merely being on PATH — the Microsoft Store stub
  answers `command -v` and then refuses to run.

---

## 10. Where to read next

| Read this | Before |
|---|---|
| [`services/credits.py`](../backend/app/services/credits.py) | Touching anything that moves credits |
| [`services/reconciliation.py`](../backend/app/services/reconciliation.py) | Deciding whether to repair drift — it deliberately does not |
| [`03-backend-architecture.md`](03-backend-architecture.md) §8 | Designing the adapter, renewal or webhooks |
| [`05-api-contract.md`](05-api-contract.md) §7 | Writing any billing endpoint |
| [`13-mvp-direction.md`](13-mvp-direction.md) | The `beta` plan, referrals and templates |
| [`18-media-asset-claim.md`](18-media-asset-claim.md) | Any new atomic claim — the pattern and the trap |

---

*Readiness note · 31 August 2026 · written before M6, from the code rather than from the plan*
