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
| **M3** | [UI Charter](08-ui-charter.md) | writing any component or stylesheet. The nine directions it was chosen from are in [`ui-directions/`](ui-directions/index.html) |

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

**Nothing blocking. M3 is under way — the editor's core is done, its interface is next.**

Projects persist as of 18 August. On the server: the five routes of [§5](05-api-contract.md), timeline validation against all eight invariants on every write, optimistic concurrency, and `project_assets` rebuilt from the document. On the client: the timeline type is **generated** from `openapi.json` and never hand-written, edits go through an Immer-patch history that makes any commit one undo step, drags stage outside the document and commit once on drop, and autosave debounces two seconds with a real `409` path. The [UI charter](08-ui-charter.md) is applied — one `@theme` block, no component touched.

What remains in M3 is the interface over that core: drag handles and snapping, the marquee, the inspector panel, transitions, the audio and text tracks, and timeline virtualisation. Each is a component over an operation that already exists and is already tested.

Everything before M3 is closed except desktop Safari, which is blocked on borrowing a Mac rather than on work.

Approved by the project lead **12 August**: it is an editor · phased release · face mapping works on both own and imported footage · web first · lip sync is in.

Approved **13 August**: phase 1 tools are captions, smart trim and colour grading · four tiers with credits underneath · monthly allowance expires, purchased credits do not · face mapping gets its own meter · Stripe **and** Razorpay both live at launch · AWS on a company account · fair-use ceiling on Unlimited · "dedicated server" reworded to dedicated priority queue.

Approved **17 August**: **A2 Studio** is the visual baseline for the design charter ([the mockups](ui-directions/ui-directions-modern/index.html)) · **per-file upload size is set per plan** — 100 MB Free, 1 GB Pro, 5 GB on the unlimited tier ([scope §3.2](02-scope-v1.md)) · the httpOnly refresh cookie introduced in [contract v1.2](05-api-contract.md) is ratified, so it stops being an implementation decision made under M2 and becomes the agreed position.

### Not blocking, but needed soon

| | What | Needed by | Owner |
|---|---|---|---|
| **1** | Open the Stripe and Razorpay accounts | Before billing can be tested end to end — external lead time, start now | Project lead |
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
