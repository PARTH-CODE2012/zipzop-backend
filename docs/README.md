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

**Diagrams:** [system overview](diagrams/system-overview.md) · [data model](diagrams/data-model.md) · [job lifecycle](diagrams/job-lifecycle.md)

### If you have fifteen minutes

Read [Phase 1 Scope](02-scope-v1.md) end to end, then [§5.2 of the vision](01-product-vision.md#52-the-two-kinds-of-ai-tool) — the two kinds of AI tool. That one distinction explains most of the architecture.

---

## The five things that shape everything

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

---

## Status

**Approved by the project lead on 12 August 2026:** it is an editor · phased release · face mapping works on both own and imported footage · web first · lip sync is in.

### Blocking

| | What | Blocks | Owner |
|---|---|---|---|
| **A** | Confirm the three phase 1 AI tools — captions, smart trim, colour grading | Which worker gets written first, and whether phase 1 needs GPU hardware | Project lead |
| **B** | Payment provider and credit pricing | Charging anyone. Everything else builds and tests with granted credits. | Project lead |
| **C** | Smart Trim: tighten a recording, or cut it to its best parts? | Whether Smart Trim is a phase 1 tool at all | Project lead |

Decision **A** is the only one that blocks starting. Full register in [the vision, §12](01-product-vision.md#12-decision-register).

### Deferred by agreement

Consent, watermarking and misuse policy for face mapping. Phase 1 stores **no facial data at all**, which is what makes deferring it safe — there is nothing to be non-compliant with yet. It needs a named owner before the face-mapping phase starts, not before launch. See [vision §9](01-product-vision.md#9-faces-consent-and-generated-video).

---

## Phases

| | Phase 1 — Launch | Phase 2 — The differentiator | Phase 3 — Breadth |
|---|---|---|---|
| **Editor** | Single-track timeline, core edits, browser preview, server export | Multiple video tracks, overlays | Speed ramps, keyframes |
| **AI** | Captions · Smart Trim · Colour Grading | Face Mapping + Lip Sync · Noise removal | Clip Finder · Templates · Upscaling |
| **New infra** | Job queue, credit ledger, ingest, export renderer | GPU cluster, face profiles, consent flow | Speaker tracking, music licensing |
| **Platform** | Web | Web | Web + iOS/Android |

Later phases add **workers, not architecture**. That is the design's main claim, and the reason for the shape of the `jobs` table.

---

## Workstreams

These run in parallel once the [API contract](05-api-contract.md) is agreed.

| Stream | Owns | Waits on |
|---|---|---|
| Backend — core | Accounts, projects, timeline persistence, credit ledger | — |
| Backend — media | Upload, probe, proxy/thumbnail/peaks, storage lifecycle | Core |
| Backend — jobs | Queue, workers, progress, WebSocket, the three tools | Core, Media |
| Backend — render | Export: timeline document → finished file | Media, timeline schema |
| Frontend | The whole editor | The contract, not the backend |

**The frontend does not wait for the backend.** Most of phase 1's frontend — timeline, playback, editing, undo — touches no server. It is built against a mock server generated from the OpenAPI schema and connected later. See [API contract §10](05-api-contract.md#10-building-against-this-before-the-backend-exists).

---

## Source material

These are the documents this set was built from. Kept for reference; **superseded** by everything above where they disagree.

- `vision.md` — v0.2 of the product definition, the draft the project lead approved

---

## Conventions

- **Times are integer milliseconds**, everywhere, on both sides. Never seconds, never floats.
- **Spatial values are normalised 0–1** relative to the canvas, never pixels. This is what makes a 480p preview and a 1080p export agree.
- API fields are `camelCase`; database columns are `snake_case`; translation happens in the serialisation layer.
- Identifiers are prefixed UUIDs: `ast_`, `prj_`, `job_`, `clp_`, `trk_`.
- Error `code` is stable and machine-readable. Branch on it, never on `message`.

---

*Documentation set v1.0 · 12 August 2026 · maintained by MMaxouB*
