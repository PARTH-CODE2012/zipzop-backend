# The Discord launch — a fifth plan, and how users are acquired

**The project lead's MVP direction, 25 August 2026, and the decisions taken from
it.** *MVP* here means **phase 1 as already scoped** — nothing in
[`02-scope-v1.md`](02-scope-v1.md) is cut. What this adds is a **$3.99 beta
plan alongside the four existing tiers** — a price charged while the rest of the
product is still being built, to get paying testers — plus a **Discord
promo-code referral scheme** and **templates**.

| | |
|---|---|
| **Status** | 🟢 **Decided.** The scope questions were delegated back and are answered below, each with its reasoning and its cost to reverse. One item stays with the project lead — §6 |
| **Changes** | Adds to [`02-scope-v1.md`](02-scope-v1.md) §3.1 and §3.3. Supersedes only the *"both providers at launch"* half of the 13 August approval |
| **Does not change** | The architecture, the tiers, the credit model, export, or M7. See §5 |
| **Blocks** | Nothing |

---

## 1. What was said

Recorded verbatim, because everything below interprets it.

> Hey man by MVP I mean the core features like auto captions reuse templates and
> AI trimming if any of these feel like taking too much time you can cut it for
> now and add something simple from your side that works fast
>
> Regarding Discord we are not building an in-app bot or anything inside Discord.
> The app will purely run on the web with Razorpay integration for the $3.99
> subscription
>
> What we are doing on Discord is collabing with server owners. We will give each
> server owner a unique promo code to announce in their server. If a user inputs
> that code on our web app they get access and the server owner gets a 15% cut
> from that $3.99 subscription. We are just using Discord to gain initial users
> for our web app
>
> So for MVP you just need to set up:
> 1 The core video tools (auto captions, templates, AI trim)
> 2 Web subscription flow with Razorpay for $3.99
> 3 A simple promo code field on web so we can track which server owner brought
> the user
>
> Let me know if this makes sense so we can move forward

Three clarifications followed: **MVP means phase 1**; **the $3.99 is added to the
existing tiers for now and removed later**; and **it is a beta price** — charged
while the rest of the product is still being built, to get paying testers whose
feedback is the actual return on it.

That last one is not a detail. It changes what the plan is *for*, and therefore
what it should be called and how much it should include — §3.

---

## 2. What this actually adds

| | Before | Now |
|---|---|---|
| **Plans** | Four — Free, Pro, Business, Studio | **Five.** A `beta` tier at $3.99, temporary, sold to the Discord audience while the product is still being built |
| **Providers** | Stripe **and** Razorpay, both live (13 August) | **Razorpay first**, Stripe deferred — the only part of that approval that moves |
| **Tools** | Captions · Smart Trim · Colour Grading | Unchanged. **Templates is added**, and it is not a fourth AI tool — §4 |
| **Acquisition** | Not addressed anywhere | Discord server owners, promo codes, **15% revenue share**. Entirely new |
| **Discord integration** | Never proposed | 🟢 **Explicitly not built.** No bot, nothing in-app. Recorded so nobody builds one |

**Two of the three named tools are already shipped.** Auto captions and AI trim
landed in M4 on 21 August and run on real speech in English, French and Hindi.
Colour Grading is shipped too and stays exactly where it is. The only new tool
work is templates.

---

## 3. The `beta` plan

### The values, and why each one

| | Value | Reasoning |
|---|---|---|
| `code` | `beta` | Changed from `starter` once the price's purpose was clear. **What is being sold is early access to a product still under construction**, and `beta` says so. It is the honest label; it explains to the customer why the price is a fifth of Pro; it explains to us why the plan later disappears; and it stays true after it does, because those users *were* the beta cohort. `starter` would have implied a finished product at a discount |
| `monthly_credits` | **800** | Free is 300 and Pro is 2 500. A straight pro-rata of Pro's price gives ~500, only 200 more than free — nobody pays for that. But the governing number here is not margin: **the return on this plan is feedback**, and a tester who runs out of credits in week two stops testing and stops reporting. 800 is roughly *"≈30 videos a month"*, the language the product already uses, and enough for someone to form a real opinion. It is deliberately generous for the price and can go **up** without much risk while the cohort is small |
| `max_export_height` | **1080** | The thing being bought is a usable output. 720p vertical in 2026 looks broken next to what the same phone shot |
| `watermark` | **`none`** | 🔴 **The single most important value here.** Removing the watermark is the main reason anyone converts off free. A paying user who still sees one feels cheated and churns in the first week — and this plan's whole purpose is converting strangers who arrived from a Discord announcement |
| `queue_priority` | **0** | ⚠️ **Not 5.** Celery's `priority_steps` are `[0, 10, 20, 30]` and `apply_async(priority=…)` passes the plan's value straight through ([`celery_app.py`](../backend/app/workers/celery_app.py)). A value between bands is silently mapped to a neighbour. Queue priority is what Pro sells; this plan buys volume, resolution and the watermark |
| `facemap_seconds` | **0** | Phase 2, same as Free |
| `fair_use_credits` | `NULL` | Only tiers advertised as unlimited carry a ceiling |
| `price_usd_cents` | **399** | |
| `price_inr_paise` | **19900** (₹199) | Same ≈50:1 ratio the other tiers use — Pro is $19.99 / ₹999 |
| `is_public` | `true`, then `false` | §3.2 |

> **The credit number is a calibrated guess, and it must not stay one.** Every
> tier's allowance derives from cost estimates, and
> `SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is flagged in
> [`11-m4-notes.md`](11-m4-notes.md) §8 as **a heuristic, not a measurement**.
> At $19.99 an error there was absorbed. At $3.99 net of commission and
> processing — about **$3.28** — it is not. Cost-per-job instrumentation moves
> from *"nice to have on the first deploy"* to **the thing that tells us whether
> this plan loses money**, and it is marked accordingly in `PHASE1-TASKS.md`.

### 3.1 It is a beta price, and it should read like one

The plan exists to fund and populate a build in progress, so two things follow
that would be wrong for an ordinary entry tier:

* **Say it is a beta.** The pricing page and the checkout should name what the
  buyer is getting — early access while the product is still being finished —
  because a customer who thinks they bought the finished thing files the missing
  export as a fault rather than as a milestone that has not landed. It is also
  the only framing under which retiring the plan later reads as planned rather
  than as a price rise.
* **Feedback needs a route back.** Nothing in the product currently collects it.
  A single link in the editor to wherever the Discord server is costs nothing and
  is the difference between paying testers and paying users — it is not in this
  scope, but it is the cheapest thing that makes the plan do its job.

### 3.2 Removing it later costs nothing, because the schema already planned for it

`plans.is_public` exists in [`billing.py`](../backend/app/models/billing.py) and
in migration `0002`. **Retiring the plan is one boolean** — the row stays, the
price list stops showing it, and everyone already subscribed keeps what they
bought. No migration, no data loss, no grandfathering logic to write.

That last property matters more for a beta price than it would for a tier:
the people on it are the ones who paid to use the product before it worked
properly, and moving them off the price they were promised is the wrong way to
thank them. Leaving them on it costs one row.

⚠️ **It is a column nothing reads yet.** Nothing in the codebase filters on
`is_public` today, because `GET /plans` does not exist until M6. That endpoint
must respect it on the day it is written, or retiring the plan will not retire
anything. Noted in the M6 checklist.

### 3.3 Two dictionaries will raise `KeyError` on the day this plan exists

[`plans.py`](../backend/app/services/plans.py) holds `CONCURRENCY_LIMITS` and
`STORAGE_QUOTA_BYTES`, both keyed by `PlanCode`, and both read with a direct
subscript — `CONCURRENCY_LIMITS[plan]`, not `.get(plan, default)`. A fifth plan
without entries in both crashes the claim path and the upload path.

Values to add with it: **concurrency** `{"analysis": 2, "render": 1,
"inference": 0}` — above Free's 1, below Pro's 3, matching where the plan sits.
**Storage** 25 GB, which keeps the `PLACEHOLDER` marker the others carry: the
per-tier storage quota is still an unanswered commercial question and this
number is no more real than the four beside it.

`PlanCode` is a Postgres enum and the primary key of `plans`, so this is
`ALTER TYPE plan_code ADD VALUE 'beta'` plus one seeded row — small, but a
migration rather than a config change.

---

## 3.4 Razorpay: the test account exists

Confirmed 25 August — **Razorpay, not Stripe.** A **test** key pair was issued
and is in the developer's local `.env`. Nothing about it is in this repository,
and nothing about it should be: `.env` is gitignored, `.env.example` carries the
variable names only, and [`07-security.md`](07-security.md) §2 already commits us
to rotating any secret that turns up in the tree.

**Two things were wired the same day, so the keys are more than a note:**

* **`assert_production_safe()` now refuses to boot a production deploy that
  carries them.** A `rzp_test_…` key in production is the failure worth
  preventing above all others here: test keys accept a card, return a success
  and move no money, so the deploy *looks* like it works right up until somebody
  asks where the revenue went. Nothing else in the system can tell the
  difference. The same guard also refuses a key with no secret beside it, and a
  key with no webhook secret — an unverified billing callback is a way to grant
  a plan for free. **Only checked when a key is present**, so today's empty
  configuration still boots. Eight cases in `tests/test_config.py`.
* **`make razorpay-check`** answers the two questions about the *account* that
  no amount of our code can: do the keys authenticate, and **will Razorpay take
  USD**. Read-only by default; `ARGS=--currency` additionally creates a
  test-mode order in INR and in USD, which is the only way to find out.

**The pair is labelled misleadingly and it is worth knowing which is which.**

| Variable | What it is |
|---|---|
| `RAZORPAY_KEY_ID` | Starts `rzp_test_` / `rzp_live_`. **Public by design** — it ships in the browser bundle, exactly like Stripe's publishable key |
| `RAZORPAY_KEY_SECRET` | The random string beside it. **A credential.** It is routinely handed over as "the test key", which makes it sound like sample data. It is not — it signs API calls against a real account |
| `RAZORPAY_WEBHOOK_SECRET` | ⚠️ **Not part of the pair, and not yet issued.** It is chosen when a webhook endpoint is created in the dashboard |

**The webhook secret is the one that is missing, and it blocks the part that
matters most.** Signature verification is the entire defence on the billing path
([`03-backend-architecture.md`](03-backend-architecture.md) §8.5: *verify
signature → store in `provider_events` → 200 immediately → process async*).
Until that endpoint is created, webhook handling can be written but not honestly
tested, and an untested signature check is indistinguishable from no signature
check.

**Test keys do not settle the USD question, but they narrow it.** `rzp_test_`
keys work in test mode only; live keys are different and come after full account
activation, and **the currencies an account may charge are an activation matter,
not an API capability**. So a USD order accepted in test mode is not proof that
production will take one.

It is still worth running — `make razorpay-check ARGS=--currency`. A **refusal**
in test mode is conclusive and arrives now rather than during M6: it means the
$3.99 is a dollar figure the account cannot take, and the choice is to activate
international acceptance or to price in INR. An acceptance leaves the question
open until the account goes live, which is the honest state of it.

---

## 4. Templates — decided as the narrow reading

**A template is a set of settings the user saves from one project and reapplies
to another.** Caption style, colour grade, transition defaults, title styling.
Not a designed library, not mood detection, not music.

**Why.** The word used was *"reuse"*, which is this. The other reading is
[`vision.md`](../vision.md) §Features 04 & 05 — a supplied library with a
recommendation engine — and it is why Templates sat in phase 3: it carries two
commercial problems that have no owner (a **licensed music library**, and
**naming templates after real people**, which is a trademark and
personality-rights exposure with no upside). Neither problem is worth taking on
to ship a launch feature.

**What it costs, under this reading: no worker, no queue, no credits, no new
job type.** A template is a subset of the timeline document written to the
user's account and read back. It is an editor feature, and it belongs in
[`02-scope-v1.md`](02-scope-v1.md) **§3.3, not §3.4** — putting it among the AI
tools would break the sentence that says all three return analysis, which is the
sentence that explains why phase 1 needs no GPU.

**Reversible at moderate cost.** If the library was what was meant, the saved
format is the same object; what gets added is who authored it and how it is
discovered. Nothing here is thrown away.

---

## 5. What does not change, and why that is the point

- **Plans were always data, never `if` statements**
  ([`03-backend-architecture.md`](03-backend-architecture.md) §2 principle 7).
  A fifth tier is a row and an enum value.
- **Razorpay first is one adapter against an interface designed for two**
  (§8.1, *"one internal model, two adapters"*). Stripe stays addable without
  reshaping anything — it is deferred, not dropped.
- **Commission is money moving**, and money is already append-only and
  double-entry (§2 principle 6). An accrual is a ledger row against a new kind
  of counterparty, not a new financial model.
- **The credit ledger, job pipeline, timeline document and export renderer** are
  untouched.

The design's central claim — *later phases add workers, not architecture* —
holds here too. This adds two tables, a form field, an enum value and a plan row.

---

## 6. The one thing still with the project lead

🔴 **Who pays the Discord server owners, on what schedule, above what threshold,
through what channel, with what tax paperwork.** Accruing a commission is
development and is decided below. *Paying* it is a commercial process with no
owner, and it has the same shape as the payment-provider applications: external,
slow, and damaging to discover late.

**Decided in the meantime so nothing is blocked**: accrue from day one as ledger
rows, expose what each owner is owed, and **pay the first cohort by hand**. That
is correct at ten server owners and wrong at a hundred, which is the point at
which the answer is needed.

### Decided here, and reversible

**The promo code grants bonus credits, not a discount.** A one-off **+300
credits** at sign-up — the same as a free month's allowance.

*Why not a discount.* A discount and a 15% commission on the same $3.99 leaves
almost nothing, and it takes it from the one number that has to cover
transcription, trimming, storage and export. Bonus credits cost variable
compute, which we control and can retune without touching a published price.

*Why it has to grant something at all.* The free tier already exists with 300
credits, so a code that only tracked would give the user no reason to type it
and the server owner nothing to announce. The offer has to be real for the
channel to work.

*Reversible at no cost* — it is a number in a table.

**What the 15% is calculated on.** The subscription price, $3.99, recomputed on
**every renewal** rather than once at signup. A commission paid once on a
recurring product misaligns the owner's incentive from the first month onward.

---

## 7. Effect on the plan

**Nothing is removed from any milestone.** M5 (export) and M7 (security) are
unaffected and were never in question — MVP means phase 1.

| Milestone | Effect |
|---|---|
| **M4** ✅ | Done. Two of the three named tools already ship |
| **M4.5** 🟠 | Unchanged, and **item 1 gets sharper**: `/projects` is a dead end, and this direction is about sending strangers from a Discord announcement to a URL. Fix it before any code is announced |
| **M5 — export** | Unchanged |
| **M6 — money** | **Grows.** Razorpay-first removes the second adapter; the `beta` plan, the promo codes, the attribution and the commission accrual all add to it. Net: larger than before |
| **New: templates** | Small. Editor feature, no server work |
| **M7 — security** | Unchanged in scope, wider in surface: a promo code is a new public input and a new money path, and self-referral, code sharing and post-payout chargebacks are worth a paragraph before launch rather than after |

Work order is unchanged. M5 was next before this message and is still next.

---

*Build note · 25 August 2026 · direction from the project lead, scope questions decided as delegated · one commercial process still unowned (§6)*
