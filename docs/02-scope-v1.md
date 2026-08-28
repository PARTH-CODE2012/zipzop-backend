# Scope — Phase 1

**Read this first. It is the short, cold list of what we are building now.**
Everything here is a commitment. Everything not here is not in phase 1, however reasonable it sounds.

| | |
|---|---|
| **Version** | 1.1 — billing folded in |
| **Date** | 13 August 2026 |
| **Depends on** | [`01-product-vision.md`](01-product-vision.md) — the *why* behind every line here |
| **Status** | **Confirmed, and added to on 25 August** — read [`13-mvp-direction.md`](13-mvp-direction.md) alongside it. Nothing here is cut. |

> **Added on 25 August 2026, not replaced.** The project lead's MVP direction
> means *phase 1 as scoped below*, plus three things this document does not
> mention: a **fifth plan at $3.99** sold to a Discord audience and removed
> later, a **promo-code referral scheme** paying server owners 15%, and
> **templates**. The only line below that moves is *"both providers at launch"*
> — Razorpay ships first and Stripe is deferred. Everything else stands as
> written.

---

## 1. Phase 1 in one paragraph

A **web** video editor. The user signs up, imports footage, arranges it on a timeline with one video track, one audio track and one text track, previews it in the browser, and exports a finished file. Three AI tools sit in the toolbar: **automatic captions**, **smart trimming**, and **colour grading**. Each analyses the footage on our servers and returns edit decisions that land on the timeline, where the user can adjust or undo them. Editing is free; the AI tools and export spend credits. Users start on a free tier and can subscribe to one of ~~three~~ **four** paid plans — a **$3.99 `beta` tier** was added on 25 August for the Discord launch and is removed once that campaign ends ([`13-mvp-direction.md`](13-mvp-direction.md) §3) — paying through ~~Stripe or Razorpay~~ **Razorpay, with Stripe deferred rather than dropped**.

No face mapping. No mobile app. That is phase 2 and phase 3.

> **Billing is in phase 1.** It was not in v1.0 of this document. The launch strategy depends on subscription revenue arriving from day one, which means payments, plans, credit allowances and the paywall are all launch scope, not follow-up. This is the single largest addition since v1.0 and it is on the critical path — nothing can go live without it.

---

## 2. The phases

| | Phase 1 — Launch | Phase 2 — The differentiator | Phase 3 — Breadth |
|---|---|---|---|
| **Editor** | Single-track timeline, core edits, browser preview, server export | Multiple video tracks, overlays | Speed ramps, keyframes |
| **AI tools** | Captions · Smart Trim · Colour Grading | **Face Mapping + Lip Sync** · Noise & echo removal | Viral Clip Finder · Templates & Recommendations · Upscaling & stabilisation |
| **Commerce** | Four tiers, credits, Stripe + Razorpay, paywall | Face-mapping meter starts being spent | — |
| **New infrastructure** | Job queue, credit ledger, ingest pipeline, export renderer, billing | GPU inference cluster, face profile storage, consent flow | Speaker tracking, music licensing, template authoring |
| **Platform** | Web | Web | Web + iOS/Android |
| **Rough size** | XL | L | M |

> **Sizes are relative, not a schedule.** No dates appear in this document because no team size or deadline has been given to us. When those arrive, phase 1 is the one to estimate first — the other two build on machinery it delivers.

---

## 3. What ships in phase 1

### 3.1 Accounts and billing

> **Added 25 August — see [`13-mvp-direction.md`](13-mvp-direction.md) §3 and §6.**
> Everything in this section stands. On top of it: a **fifth `beta` plan at
> $3.99 / ₹199** (800 credits, 1080p, no watermark, free-tier queue priority).
> It is **a beta price, charged while the rest of the product is still being
> built**, to get paying testers whose feedback is the return on it — which is
> why it is called `beta`, why the allowance is generous for the price, and why
> it is retired later by setting `plans.is_public` to false. The column already
> exists, so nobody loses what they bought. Plus a **promo-code
> field at sign-up** that attributes the user to a Discord server owner
> permanently and pays them **15% of every renewal**, and grants the user **+300
> bonus credits** rather than a discount. **Razorpay ships first; Stripe is
> deferred, not dropped.**

- Sign up and sign in with email and password
- Session that survives a page reload
- Credit balances visible in the interface — monthly allowance, purchased credits, and their combined total
- **Four tiers**: Free, Pro, Business, Studio ([`01-product-vision.md`](01-product-vision.md) §8.1)
- **Pricing page** driven by the plans table, in USD or INR
- **Subscribe, upgrade, downgrade, cancel** — checkout on the provider's hosted page, never our own card form
- **Buy top-up credits** that never expire
- **Both payment providers live**: Stripe for USD/global, Razorpay for INR/India. IP suggests the currency, the user can change it
- **Monthly renewal**: allowance granted, unused allowance expired, purchased credits untouched
- **Paywall states**: hitting the credit limit, asking for 4K on a plan that does not include it, running out mid-project
- Credit history — every movement, with which bucket it came from

Not in phase 1: teams and shared balances, invoicing documents, tax configuration (needs an owner outside the dev team — [`03-backend-architecture.md`](03-backend-architecture.md) §8.6).

### 3.2 Media

- Import video, audio and images from the user's device
- A media bin per project showing what has been imported
- Every upload is probed on arrival, and gets a low-resolution **proxy** for preview, a **thumbnail**, and **waveform peaks** for the timeline
- Delete an imported file

**Accepted on arrival:** MP4/MOV/WebM video, MP3/WAV/M4A audio, JPEG/PNG images.

**Maximum file size — confirmed by the project lead on 17 August 2026. It is per plan, not global.**

| | Free | Pro | Business | Studio |
|---|---|---|---|---|
| **Per file** | 100 MB | 1 GB | 2 GB \* | 5 GB |

\* **Business is ours, not the lead's.** He named three plans — Free, Pro and "Enterprise Unlimited" — against the four in §8.1. Studio is the tier advertised as unlimited, so 5 GB is recorded there on that reading, which leaves Business uncovered. 2 GB sits between its neighbours and matches the global default this table replaces, so it is a defensible interim rather than a guess. It is one constant in `backend/app/services/plans.py` and one cell here — decision **P**, to be corrected whenever the answer arrives rather than chased.

"Unlimited" describes the **monthly video allowance**, not the file: a 5 GB per-file ceiling still applies on that tier, and the fair-use clause in §8.1 is what bounds the volume.

The 2 GB global default proposed in v1.0 is superseded. The ceiling rising to 5 GB is an ingest concern rather than a scope one — multipart upload, probe timeouts and proxy transcode time are all sized against the largest accepted file, not the average.

**Still proposed, still need confirming:** max 60 minutes per video · up to 4K source.

### 3.3 The editor

| Area | Phase 1 | Not phase 1 |
|---|---|---|
| **Projects** | Create, rename, duplicate, delete, autosave, reopen where you left off · **save a template and reapply it** (added 25 August) | ~~Templates~~ *a supplied template library*, folders, sharing |
| **Timeline** | One video track, one audio track, one text track. Playhead, zoom, snap, scrub | Multiple video tracks, overlay tracks, track groups |
| **Clip edits** | Split, trim both ends, move, reorder, duplicate, delete | Ripple/roll edits, magnetic timeline |
| **Clip properties** | Volume, speed (fixed rate), rotate, flip, crop and reframe to the project aspect ratio | Speed ramps, keyframed properties, motion paths |
| **Audio** | Per-clip volume, fade in/out, one music track, detach audio from video | Ducking, EQ, multi-track mixing |
| **Text** | Add a title or overlay — text, font, size, colour, position, basic animation in/out | Rich text, custom fonts, path animation |
| **Transitions** | Cut, fade to black, cross dissolve | Wipes, zooms, 3D transitions |
| **Undo/redo** | Deep history covering AI results as single undoable steps | Named history states, branching |
| **Preview** | Real-time playback of the whole timeline with everything applied, in the browser, on proxy media | 4K preview, external monitor output |
| **Export** | 720p/1080p/4K by plan · 9:16, 16:9, 1:1 · MP4/H.264 · watermark by plan · progress and notification | Custom bitrates, ProRes, GIF |

**Templates, added 25 August.** A template is **the user's own settings, saved
and reapplied** — caption style, colour grade, transition defaults, title
styling. It is a subset of the timeline document stored against the account, so
it needs no worker, no queue, no credits and no new job type.

What is still *not* phase 1 is the other meaning of the word: a **supplied
library** of designed templates with mood detection, which is
[`vision.md`](../vision.md) §Features 04 & 05 and carries two commercial problems
with no owner — a licensed music library, and naming templates after real people.
Reasoning in [`13-mvp-direction.md`](13-mvp-direction.md) §4.

### 3.4 The three AI tools

All three are **analysis** tools: they return decisions, not media. That is what makes phase 1 shippable without a GPU cluster.

> **Unchanged by the 25 August direction.** All three ship, and two of them
> already do. **Templates is not a fourth AI tool** — it returns no analysis and
> runs no job, so it sits in §3.3 with the editor. Keeping this section at three
> is what keeps the sentence above true, and that sentence is the reason phase 1
> needs no GPU ([`13-mvp-direction.md`](13-mvp-direction.md) §4).

#### Automatic Captions

Transcribes the speech, times every word, detects emphasis. Lands as a **text track**, word by word, in the chosen style. Every word is editable.

- Ships with 3 caption styles
- Transcription in the language spoken. **Translation is not in phase 1.**
- ~~Language list~~ — **closed 21 August: English, French and Hindi.** The vision doc's "30+ languages" conflated transcribing with translating; phase 1 does only the first, so this decides what speech is accepted and nothing else. Adding a fourth is a list plus somebody who speaks it checking the fillers ([`11-m4-notes.md`](11-m4-notes.md) §3)
- Captions are burned into the picture at export. A separate subtitle file is phase 2.

#### Smart Trimming

Finds silences, pauses, filler sounds, stutters and repeated takes. Lands as **real cuts on the timeline** — the clip is split and dead segments removed. Every cut is draggable, and the whole operation undoes in one step.

- Ships with three strengths: light, medium, aggressive
- Applied straight away, adjusted afterwards — no separate review screen
- **Scoped as tightening a recording, not summarising it.** See open decision C: if the promise is really "10 minutes to 2", that is a different tool and it does not ship in phase 1.

#### Colour Grading

Examines the footage's lighting and colour and returns the profile that suits it, with a strength value. Applied **live in the browser preview**, baked in at export.

- Ships with 5 looks, exact list to confirm
- Strength slider, per clip
- User-supplied LUT files are phase 3

### 3.5 Behind the scenes

Not visible in the interface, but part of phase 1 because everything later depends on it:

- **Job pipeline** — queue, workers, progress, retries, cancellation, priority bands by plan
- **Credit ledger** — three buckets, reserve on start, release on failure, full audit trail
- **Billing service** — one internal model, one adapter per provider, webhook handling with replay protection
- **Renewal scheduler** — monthly grant and expiry, driven by webhooks with an hourly sweep as the safety net
- **Live updates** — WebSocket push of job progress, credits and subscription changes
- **Export renderer** — the server-side pipeline that bakes the timeline into a file, and where plan limits are enforced
- **Storage lifecycle** — where media lives, what gets cleaned up and when
- **Cost instrumentation** — cost per job by tool, from the first deploy, so the tiers can be re-priced on measurement rather than estimate

---

## 4. What phase 1 does not include

Named explicitly so nobody has to guess.

| Not in phase 1 | Lands in |
|---|---|
| Face mapping, lip sync, any facial data at all | Phase 2 |
| GPU inference cluster | Phase 2 |
| Teams, shared balances, seat billing | Not scoped |
| Invoicing documents, tax configuration | Needs an owner outside the dev team |
| Noise removal, echo removal | Phase 2 |
| Video upscaling, stabilisation | Phase 3 |
| Viral Clip Finder, speaker tracking | Phase 3 |
| Templates, recommendation engine, music library | Phase 3 |
| iOS and Android | Phase 3 |
| Multiple video tracks, overlays | Phase 2 |
| Caption translation, subtitle file export | Phase 2 |
| Custom LUTs, brand kits | Phase 3 |
| Publishing to TikTok/YouTube/Instagram | Not scoped — open decision I |
| Collaboration, teams, shared balances | Not scoped |
| Green screen, keyframes, multi-camera | Not scoped |

---

## 5. Why these three tools

This is a proposal. Here is the reasoning, so it can be argued with.

**They are the three that make the editor feel worth using on day one.** A creator's actual complaints are "cutting the umms takes an hour", "my captions take longer than filming", and "my phone footage looks flat". These three answer all of them.

**They need no GPU cluster.** All three analyse and return data. Phase 1 therefore ships on modest compute, and the expensive GPU infrastructure arrives in phase 2 with the feature that actually justifies it. Building it in phase 1 would mean paying for idle hardware.

**They keep facial data out of phase 1 entirely.** No face profiles, no biometric storage, no consent flow, no synthetic-media marking — none of it exists in the phase 1 database. That is what makes deferring the compliance work safe rather than reckless: there is nothing to be non-compliant with yet, and phase 1's build time is the window in which that work gets done.

**They exercise the whole machine.** Between them these three tools use every piece of infrastructure phase 2 will need — the job queue, progress reporting, credit reservation, result application, undo integration, and the export renderer. Phase 2 then adds a new *kind of worker* to a pipeline that already works in production, rather than building the pipeline and the hardest AI feature at the same time.

**The cost of being wrong is low.** If the project lead swaps one tool for another, the architecture does not change — only which worker gets written first.

### What this leaves exposed

Phase 1 ships without the differentiator. Its story is "the editor that does the boring work for you", not "the face-swap product". If that is a problem for how launch is being sold, it is a conversation to have now rather than after the build.

---

## 6. Done means

Phase 1 is finished when a person who has never seen the product can, without help:

1. Sign up, and land in an empty project
2. Import a 10-minute talking-head recording from their laptop
3. See it on the timeline with a waveform, and scrub through it smoothly
4. Run Smart Trim, watch progress, and see the cuts appear — then drag one to fix it
5. Run Captions, see the text track appear in time with the speech, and correct a misspelled name
6. Run Colour Grading, see the picture change immediately in the preview, and pull the strength back
7. Add a title, a fade at the start, and a music track
8. Undo the last six things, then redo them
9. Close the tab, come back an hour later, and find the project exactly as they left it
10. Export a 9:16 MP4 at the best resolution their plan allows, and download it
11. See their credit balance go down by the right amount, and go back up when a job fails
12. Run out of credits, understand exactly why, and see what upgrading would give them
13. Subscribe to Pro — in USD or in INR — and have the new allowance appear within seconds
14. Export again and find the watermark gone

If any step needs an explanation, it is not done.

---

## 7. Who builds what

Workstreams that can run in parallel once [`05-api-contract.md`](05-api-contract.md) is agreed. The contract is what lets these proceed without waiting on each other.

| Workstream | Owns | Depends on |
|---|---|---|
| **Backend — core** | Accounts, projects, timeline persistence, credit ledger | — |
| **Backend — media** | Upload, probe, proxy/thumbnail/peaks generation, storage lifecycle | Core (accounts) |
| **Backend — jobs** | Queue, workers, progress, priority, WebSocket, the three tool workers | Core, Media |
| **Backend — render** | Export pipeline: timeline document → finished file, plan gating, watermark | Media, and the timeline document schema |
| **Backend — billing** | Plans, subscriptions, both providers, webhooks, renewal and expiry | Core (ledger) |
| **Frontend** | The whole editor: timeline UI, playback engine, tool integration, account and billing screens | The API contract, not the backend itself |

**The frontend does not wait for the backend.** With the contract agreed, the editor is built against mocked responses and connected later. Most of phase 1's frontend work — timeline, playback, editing, undo — touches no server at all.

**Billing is the one stream that cannot slip.** It has an external dependency the others do not: provider accounts, business verification and, for Razorpay, Indian entity documentation, all of which take real calendar time and none of which the development team controls. Start the account applications before writing the code.

---

## 8. Blocking

**Still nothing.** The 25 August direction added scope but blocked no work: what
a template means, what the promo code grants, and how the `beta` plan is
priced and later retired were all decided on the day
([`13-mvp-direction.md`](13-mvp-direction.md)).

Three things still need to happen, and none of them stops work:

| | What | Needed by | Owner |
|---|---|---|---|
| **1** | ~~Open the Stripe and Razorpay accounts~~ → **the Razorpay account**, and confirm it can charge in **USD** — $3.99 is a dollar price on an Indian processor and international activation is a separate application. Stripe is deferred, so its application can wait | Before billing can be tested end to end | Project lead — external lead time, start now |
| **2** | Smart Trim: tighten a recording, or cut it to its best parts? | Before we describe it publicly | Project lead |
| **3** | 🔴 **How Discord server owners actually get paid** — schedule, threshold, channel, tax | Before the tenth server owner, not the first. Accrual and manual payment of the first cohort are already decided, which is what keeps this off the critical path | **Unowned** — needs one, like the provider applications did |

The credit numbers per tier are proposals derived from estimated costs ([`03-backend-architecture.md`](03-backend-architecture.md) §5.5). They live in one table and one module, so re-pricing once real cost-per-job is measured is a data change, not a rebuild. Build against them as they stand.

---

*AI Video Editor · Phase 1 Scope v1.0 · 12 August 2026*
