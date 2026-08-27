# AI Video Editor — Documentation

Everything needed to build this product. Start here.

**What we are building:** a web video editor — CapCut-style timeline, tracks, playhead — where AI tools sit in the toolbar alongside the ordinary ones. Trim the silences, generate the captions, grade the picture. The results land on the timeline, where they can be adjusted or undone like any other edit.

---

## Read in this order

| | Document | For | Read it to find out |
|---|---|---|---|
| **1** | [Phase 1 Scope](02-scope-v1.md) | Everyone | What we are building **now**, and what we are not |
| **2** | [Product Vision](01-product-vision.md) | Everyone | What the product does, why, and every decision still open |
| **3** | [API Contract](05-api-contract.md) | Both teams | The interface both sides build against |
| **4** | [Backend Architecture](03-backend-architecture.md) | Backend | Data model, job pipeline, media, rendering, infrastructure |
| **5** | [Frontend Architecture](04-frontend-architecture.md) | Frontend | Timeline state, playback engine, undo, tool integration |
| **6** | [Security & Penetration Testing](07-security.md) | Everyone, before launch | What M7 reviews, what it attacks, and what blocks the release |
| **7** | [UI Charter](08-ui-charter.md) | Frontend, before writing any component | The palette, typeface, spacing, component states and motion — and the five rules that settle arguments |

**Diagrams:** [system overview](diagrams/system-overview.md) · [data model](diagrams/data-model.md) · [job lifecycle](diagrams/job-lifecycle.md)

**Build notes**, written as milestones land — decisions made, documents changed, and traps the next milestone would otherwise rediscover:

| | Milestone | Read it before |
|---|---|---|
| **M1** | [Compositor spike](../frontend/src/editor/playback/README.md) | touching the renderer or the playback clock |
| **M2** | [Accounts, upload, ingest](06-m2-notes.md) | starting M3 |
| **M3** | [Editing that survives a reload](09-m3-notes.md) | starting M4 — and the [UI Charter](08-ui-charter.md) before writing any component. The nine directions it was chosen from are in [`ui-directions/`](ui-directions/index.html) |
| **M4 prep** | [M4 readiness](10-m4-readiness.md) | starting M4 — what is already built, what the contract already specifies, and two open decisions found while checking |
| **M4** | [The job pipeline and the three tools](11-m4-notes.md) | touching jobs, credits, a worker or a tool result. Why the enqueue moved outside the transaction, and the asset-time conversion that makes a caption land in the right place |
| **M4.5** ✅ | [The interface pass](12-m4-5-interface-pass.md) — its findings · [build notes](14-m4-5-notes.md) — what they became | touching any editor screen. **Done 25 August**: the mode rail, the transport under the picture, manual colour and audio, typeable numeric fields, a real project list and a draggable timeline. The notes carry the defect this pass introduced — a percentage field that silently wrote 100 % when you typed 42 — and why the fixture server is not proof of anything the backend does |
| **Reliability** 🟢 | [Pipeline reliability](16-pipeline-reliability-notes.md) | **touching ingest, upload-complete, or job enqueue.** An outside audit's core finding — database state, storage and workers can drift apart — checked against the running code and fixed: ingest's retry was declared but never reachable, the upload-complete endpoint enqueued before its own commit, and nothing swept a job whose Celery send failed. One gap named and left open on purpose: `MediaAsset` has no atomic claim, so a stuck `probing` asset is reported, not auto-retried |
| **M5 prep** 🟢 | [M5 readiness](15-m5-readiness.md) | **starting M5.** What export inherits already built (the whole M4 pipeline, the enum, the pricing, the plan columns), what the contract already decides, and 🔴 **the one thing that blocks it**: the renderer cannot read a `.cube` file — they live under `frontend/public/` and the backend image is built from `backend/` |
| **Verification** 🟢 | [The first real test run since M4](17-first-real-test-run.md) | **before trusting any note written between M4 and 27 August.** The suite finally executed — 231 passed — and found four defects that two careful re-reads had not: a test that was wrong about code that was right, a `reconciliation` queue added everywhere except the script people actually run, a lavfi path escape one level short, and a red CI nobody could see. Three of the four live in the seams *between* files, which is the part re-reading cannot reach |
| **MVP** 🟢 | [The Discord launch](13-mvp-direction.md) | **touching plans, billing, sign-up or templates.** The 25 August direction: a fifth `beta` plan at $3.99 added to the four tiers and retired later, a Discord promo-code revenue share, and templates in the narrow sense. Every value it sets — 800 credits, no watermark, queue priority **0 and not 5** — is argued there, along with the two dictionaries in `plans.py` that raise `KeyError` the day a fifth plan exists |

### If you have fifteen minutes

Read [Phase 1 Scope](02-scope-v1.md) end to end, then [§5.2 of the vision](01-product-vision.md#52-the-two-kinds-of-ai-tool) — the two kinds of AI tool. That one distinction explains most of the architecture.

---

## The six things that shape everything

Read these before disagreeing with any specific decision — most objections are answered by one of them.

**1 · It is an editor, not a generator.**
The user is not submitting a video and receiving a finished one. They are editing, and the AI does the tedious parts on request. Everything follows from this: the client owns a timeline document, results are undoable edits, nothing is baked until export.

**2 · AI tools come in two kinds, and it matters more than anything else.**
Tools that return **decisions** — cut points, transcripts, a colour profile — are cheap, fast, and leave the result fully editable. Tools that return **pixels** are slow, expensive and need GPUs. Phase 1 ships only the first kind, which is why it needs no GPU cluster at all. Every new tool should be pushed into the first group if it possibly can be.

**3 · The client owns the timeline; the server owns media, jobs and money.**
Dragging a clip is not a network call. The server stores and validates the timeline document but never computes an edit. This is what makes the editor feel like an editor.

**4 · Original media is immutable.**
Nothing overwrites an upload. Any tool that changes pixels produces a *new* asset recording what it came from. "Revert to original" is therefore always free and always available.

**5 · Preview runs on proxies; export runs on originals.**
Every upload gets a 480p proxy at ingest. The browser composites those live — grades, captions, transitions, all instant and free. Export assembles the real media server-side, once. The colour maths is shared between the two, and must stay shared.

**6 · We show videos, we count credits.**
Tiers are advertised as "≈30 videos/month" because that is what a creator understands, but every internal limit is credits. A literal video counter breaks the moment someone re-runs captions on a clip they have not finished. Credits come in three kinds — a monthly allowance that expires, purchased credits that never do, and a separate meter for face mapping — and jobs always spend the soonest to expire first.

---

## Status

**The Discord launch was added on 25 August — [`13-mvp-direction.md`](13-mvp-direction.md).** *MVP* means phase 1 as already scoped: **nothing is cut**. What it adds is a fifth **`beta` plan at $3.99** — a beta price charged while the rest of the product is built, to get paying testers whose feedback is the return on it, retired later through the `is_public` column that already exists; a **Discord promo-code scheme** paying server owners 15% of every renewal; and **templates**, in the narrow sense of reusing your own settings. Razorpay ships first and Stripe is deferred — the only part of the 13 August approval that moves. **No architecture changes**: plans were always data and the provider model always had adapters. Nothing is blocked; one commercial process (paying the server owners) still needs an owner.

**An outside audit's core finding was checked and fixed on 26 August — [`16-pipeline-reliability-notes.md`](16-pipeline-reliability-notes.md).** *"Make the upload → processing → job pipeline reliable and self-recovering"*: most of the state machine it asked for already existed under different names, and the concurrency M4 built for jobs was already correct — the gap was that M2's older upload and ingest path never received the same treatment. Ingest's retry was declared but no code path ever reached it; the upload-complete endpoint enqueued a worker before its own transaction committed; nothing swept a job whose Celery send silently failed. All three fixed, plus a five-minute sweep that re-sends stuck work — safe because the existing atomic claim makes re-sending a job that was never actually stuck a harmless no-op. One gap named rather than papered over: `MediaAsset` has no equivalent claim, so a stuck upload-processing asset is reported, not auto-retried, until it does.

**M4 is done: the pipeline, all three tools, and the interface over them.**

Editing survives a reload, verified end to end on a running stack. The server has the five project routes of [§5](05-api-contract.md), timeline validation against all eight invariants on every write, optimistic concurrency and `project_assets` rebuilt from the document. The client has a generated timeline type, undo through Immer patches, the full set of editing operations, drag with snapping, a marquee, an inspector, three lanes, virtualisation, and autosave with a real conflict path. The [UI charter](08-ui-charter.md) is applied.

**The job pipeline is live** — `POST /jobs` and `/jobs/estimate` sharing one cost function, credits reserved and refunded against a ledger the database keeps honest, a worker claim no two workers can win, progress on Redis, and colour analysis running through all of it on real media. Written up in [`11-m4-notes.md`](11-m4-notes.md).

**Proven in a browser, not only in tests**: add a clip, run Captions, watch it work, see sixteen words land on the text track, correct one, and find the correction still there after a reload. Both open decisions were closed on 21 August — the transcription engine (self-hosted `faster-whisper`, behind one function) and the language list (**English, French, Hindi**).

That browser run also found what no unit test could: a machine without WebGL2 took the *whole editor* down rather than losing the picture. The compositor having no fallback is deliberate; the editor going with it was not. Both are in [`11-m4-notes.md`](11-m4-notes.md) §4.

Left for M4: the mock fixtures, and two of the three caption styles — design work rather than engineering. M5 is unblocked.

✅ **M4.5 is done — 25 August.** All seven items of [`12-m4-5-interface-pass.md`](12-m4-5-interface-pass.md), written up in [`14-m4-5-notes.md`](14-m4-5-notes.md). The editor is no longer reachable only by knowing a URL: `/projects` is a real list instead of a stub reading *"Built in M3."*. The left panel is a **mode rail** that grows by one icon per tool rather than one stacked panel, so phase 2's four tools already have somewhere to go; the right panel is the inspector and nothing else. Colour can be graded **by hand** from the five shipped looks instead of by spending credits on an analysis job. The transport sits under the picture with the four controls that previously existed only as shortcuts. The timeline's height is the user's to set.

**301 tests, up from 236**, plus a browser run on Windows through a new fixture server — which is also the mock server the checklist had carried unticked since M0. That run found what the tests could not: a percentage field that silently wrote **100 %** when you typed `42`, from the control added specifically so values could be set exactly. Both it and a toolbar label pointing at a panel that no longer exists are in the notes. ⚠️ **M4.5 has not been through `make e2e`** — that needs Docker, and it should happen before M5 closes.

Left open on purpose: the text track's font, size and colour controls, the IndexedDB mirror (💤), and transitions in the preview — all three in [`09-m3-notes.md`](09-m3-notes.md) §5, along with the nine defects the M3 audit found, one of them a security defect in M2's auth code.

Everything before M3 is closed except desktop Safari, which is blocked on borrowing a Mac rather than on work.

Approved by the project lead **12 August**: it is an editor · phased release · face mapping works on both own and imported footage · web first · lip sync is in.

Approved **13 August**: phase 1 tools are captions, smart trim and colour grading · four tiers with credits underneath · monthly allowance expires, purchased credits do not · face mapping gets its own meter · Stripe **and** Razorpay both live at launch · AWS on a company account · fair-use ceiling on Unlimited · "dedicated server" reworded to dedicated priority queue.

Approved **17 August**: **A2 Studio** is the visual baseline for the design charter ([the mockups](ui-directions/ui-directions-modern/index.html)) · **per-file upload size is set per plan** — 100 MB Free, 1 GB Pro, 5 GB on the unlimited tier ([scope §3.2](02-scope-v1.md)) · the httpOnly refresh cookie introduced in [contract v1.2](05-api-contract.md) is ratified, so it stops being an implementation decision made under M2 and becomes the agreed position.

Directed **25 August**: **MVP means phase 1** — nothing is cut, and export and M7 are unaffected · a **fifth `beta` plan at $3.99 / ₹199** is added beside the four tiers and **removed later**, which the `plans.is_public` column already supports · **Razorpay first, Stripe deferred**, which is the only part of the 13 August "both providers at launch" approval that moves — **test keys received the same day**, held in the developer's `.env` and deliberately nowhere in this repository · **no Discord integration is built at all** — no bot, nothing in-app · acquisition is **Discord server owners with unique promo codes** paying them **15% of every renewal**, not just the first.

Decided **25 August**, delegated by the project lead and reasoned in [`13-mvp-direction.md`](13-mvp-direction.md): a **template is the user's own saved settings**, not a supplied library — which keeps the licensed-music and real-person-naming problems out of phase 1 · the **promo code grants +300 bonus credits, not a discount**, because a discount and a 15% commission on the same $3.99 leave almost nothing · `beta` gets **800 credits, 1080p, no watermark, queue priority 0** · commission **accrues from day one and the first cohort is paid by hand**, so the unowned payout process blocks nothing.

### Not blocking, but needed soon

| | What | Needed by | Owner |
|---|---|---|---|
| **1** | ~~Open the Stripe and Razorpay accounts~~ → 🟡 **Razorpay test keys received 25 August.** Still needed: **the webhook secret** (a third secret, created with the webhook endpoint — without it the signature check cannot be tested) and confirmation that a **live** account may charge in **USD**, which test keys cannot answer | Before billing can be tested end to end | Project lead |
| **1b** | 🔴 **Who pays the Discord server owners their 15%**, on what schedule, above what threshold, with what tax paperwork | Before the tenth server owner, not the first — accrual and manual payment of the first cohort are decided, so nothing is blocked meanwhile | **Unowned** — needs one |
| **2** | Smart Trim: tighten a recording, or cut it to its best parts? | Before we describe it publicly | Project lead |
| **3** | Storage retention policy | Before launch. Largest recurring cost, and it only grows. | Project lead |
| **3b** | **Storage quota per tier** 🟠 | **Still now.** The 17 August answer set the size of a *single upload*; this is the *total* a tier may hold, which is a different number and still unstated. The values in `backend/app/services/plans.py` are marked `PLACEHOLDER` and must not ship. | Project lead |
| **3c** | **A palette, a typeface, and the visual states** (clip selected, clip dragging, track muted) | 🟢 **Closed 17 August** — A2 Studio approved, and [`08-ui-charter.md`](08-ui-charter.md) written from it. All four timeline states answered in §9. Applying the tokens to `frontend/src/styles/globals.css` is an M3 task, not a question for the lead. | Done |
| **3d** | Per-file upload limit for Business ⚪ | **Not urgent, not being chased.** The 17 August limits name three plans against four tiers, so we set Business to 2 GB ourselves and carried on. Decision **P** — mention it next time it comes up. | Project lead, eventually |
| **4** | Who owns tax — Indian GST, EU VAT | Before launch. Not a development task. | Project lead |

Credit values per tier are proposals derived from estimated costs. They live in one table and one module, and cost-per-job is instrumented from the first deploy, so re-pricing on real measurements is a data change. Full register in [the vision, §12](01-product-vision.md#12-decision-register).

### Deferred by agreement

Consent, watermarking and misuse policy for face mapping. Phase 1 stores **no facial data at all**, which is what makes deferring it safe — there is nothing to be non-compliant with yet. It needs a named owner before the face-mapping phase starts, not before launch. See [vision §9](01-product-vision.md#9-faces-consent-and-generated-video).

---

## Phases

| | Phase 1 — Launch | Phase 2 — The differentiator | Phase 3 — Breadth |
|---|---|---|---|
| **Editor** | Single-track timeline, core edits, browser preview, server export | Multiple video tracks, overlays | Speed ramps, keyframes |
| **AI** | Captions · Smart Trim · Colour Grading | Face Mapping + Lip Sync · Noise removal | Clip Finder · Templates · Upscaling |
| **Commerce** | Four tiers, credits, Stripe + Razorpay, paywall | Face-mapping meter starts being spent | — |
| **New infra** | Job queue, credit ledger, ingest, export renderer, billing | GPU cluster, face profiles, consent flow | Speaker tracking, music licensing |
| **Security** | M7 — full review and penetration test before launch, then standing CI gates | The same pass again: facial data changes the risk picture more than anything else planned | — |
| **Platform** | Web | Web | Web + iOS/Android |

Later phases add **workers, not architecture**. That is the design's main claim, and the reason for the shape of the `jobs` table.

---

## Workstreams

These run in parallel once the [API contract](05-api-contract.md) is agreed.

| Stream | Owns | Waits on |
|---|---|---|
| Backend — core | Accounts, projects, timeline persistence, credit ledger | — |
| Backend — media | Upload, probe, proxy/thumbnail/peaks, storage lifecycle | Core |
| Backend — jobs | Queue, workers, progress, priority, WebSocket, the three tools | Core, Media |
| Backend — render | Export: timeline document → finished file, plan gating, watermark | Media, timeline schema |
| Backend — billing | Plans, subscriptions, both providers, webhooks, renewal | Core (ledger) |
| Frontend | The whole editor, plus billing screens | The contract, not the backend |

**The frontend does not wait for the backend.** Most of phase 1's frontend — timeline, playback, editing, undo — touches no server. It is built against a mock server generated from the OpenAPI schema and connected later. See [API contract §11](05-api-contract.md#11-building-against-this-before-the-backend-exists).

**Billing is the one stream with an external dependency.** Provider accounts, business verification and Indian entity documentation for Razorpay all take calendar time nobody on the team controls. Start those applications before writing the code.

---

## Source material

These are the documents this set was built from. Kept for reference; **superseded** by everything above where they disagree.

- `vision.md` — v0.2 of the product definition, the draft the project lead approved

---

## Conventions

- **Times are integer milliseconds**, everywhere, on both sides. Never seconds, never floats.
- **Money is integer minor units** — cents, paise — with its currency beside it. Never floats.
- **Spatial values are normalised 0–1** relative to the canvas, never pixels. This is what makes a 480p preview and a 1080p export agree.
- API fields are `camelCase`; database columns are `snake_case`; translation happens in the serialisation layer.
- Identifiers are prefixed UUIDs: `usr_`, `ast_`, `prj_`, `job_`, `clp_`, `trk_`, `pay_`. The format is the prefix, an underscore, then the canonical lowercase UUID — `usr_9b1d0c4e-3f2a-4c81-9d77-2e6b5a1f0c34`. Fixed during M2 in `backend/app/api/ids.py`; the contract's examples are truncated and never pinned it down.
- Error `code` is stable and machine-readable. Branch on it, never on `message`.
- Plan limits are enforced **server-side**. Client-side gating exists so the interface can grey a button, never as the only check.

---

*Documentation set v1.2 · 17 August 2026 · maintained by MMaxouB*
