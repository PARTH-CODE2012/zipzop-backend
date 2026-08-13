# Scope — Phase 1

**Read this first. It is the short, cold list of what we are building now.**
Everything here is a commitment. Everything not here is not in phase 1, however reasonable it sounds.

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 12 August 2026 |
| **Depends on** | [`01-product-vision.md`](01-product-vision.md) — the *why* behind every line here |
| **Status** | Editor scope firm. AI tool selection is a proposal awaiting the project lead (decision A). |

---

## 1. Phase 1 in one paragraph

A **web** video editor. The user signs up, imports footage, arranges it on a timeline with one video track, one audio track and one text track, previews it in the browser, and exports a finished file. Three AI tools sit in the toolbar: **automatic captions**, **smart trimming**, and **colour grading**. Each analyses the footage on our servers and returns edit decisions that land on the timeline, where the user can adjust or undo them. Editing is free; the AI tools spend credits.

No face mapping. No mobile app. That is phase 2 and phase 3.

---

## 2. The phases

| | Phase 1 — Launch | Phase 2 — The differentiator | Phase 3 — Breadth |
|---|---|---|---|
| **Editor** | Single-track timeline, core edits, browser preview, server export | Multiple video tracks, overlays | Speed ramps, keyframes |
| **AI tools** | Captions · Smart Trim · Colour Grading | **Face Mapping + Lip Sync** · Noise & echo removal | Viral Clip Finder · Templates & Recommendations · Upscaling & stabilisation |
| **New infrastructure** | Job queue, credit ledger, ingest pipeline, export renderer | GPU inference cluster, face profile storage, consent flow | Speaker tracking, music licensing, template authoring |
| **Platform** | Web | Web | Web + iOS/Android |
| **Rough size** | L | L | M |

> **Sizes are relative, not a schedule.** No dates appear in this document because no team size or deadline has been given to us. When those arrive, phase 1 is the one to estimate first — the other two build on machinery it delivers.

---

## 3. What ships in phase 1

### 3.1 Accounts

- Sign up and sign in with email and password
- Session that survives a page reload
- Credit balance visible in the interface
- Buying credits — **blocked on decision B** (no payment provider chosen)

### 3.2 Media

- Import video, audio and images from the user's device
- A media bin per project showing what has been imported
- Every upload is probed on arrival, and gets a low-resolution **proxy** for preview, a **thumbnail**, and **waveform peaks** for the timeline
- Delete an imported file

**Accepted on arrival (proposed defaults, need confirming):** MP4/MOV/WebM video, MP3/WAV/M4A audio, JPEG/PNG images · max 2 GB per file · max 60 minutes per video · up to 4K source.

### 3.3 The editor

| Area | Phase 1 | Not phase 1 |
|---|---|---|
| **Projects** | Create, rename, duplicate, delete, autosave, reopen where you left off | Templates, folders, sharing |
| **Timeline** | One video track, one audio track, one text track. Playhead, zoom, snap, scrub | Multiple video tracks, overlay tracks, track groups |
| **Clip edits** | Split, trim both ends, move, reorder, duplicate, delete | Ripple/roll edits, magnetic timeline |
| **Clip properties** | Volume, speed (fixed rate), rotate, flip, crop and reframe to the project aspect ratio | Speed ramps, keyframed properties, motion paths |
| **Audio** | Per-clip volume, fade in/out, one music track, detach audio from video | Ducking, EQ, multi-track mixing |
| **Text** | Add a title or overlay — text, font, size, colour, position, basic animation in/out | Rich text, custom fonts, path animation |
| **Transitions** | Cut, fade to black, cross dissolve | Wipes, zooms, 3D transitions |
| **Undo/redo** | Deep history covering AI results as single undoable steps | Named history states, branching |
| **Preview** | Real-time playback of the whole timeline with everything applied, in the browser, on proxy media | 4K preview, external monitor output |
| **Export** | 720p/1080p · 9:16, 16:9, 1:1 · MP4/H.264 · progress and notification | 4K export, custom bitrates, ProRes, GIF |

### 3.4 The three AI tools

All three are **analysis** tools: they return decisions, not media. That is what makes phase 1 shippable without a GPU cluster.

#### Automatic Captions

Transcribes the speech, times every word, detects emphasis. Lands as a **text track**, word by word, in the chosen style. Every word is editable.

- Ships with 3 caption styles
- Transcription in the language spoken. **Translation is not in phase 1.**
- Language list — blocked on the "30+ languages" question in the vision doc
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

- **Job pipeline** — queue, workers, progress, retries, cancellation
- **Credit ledger** — reserve on start, charge on success, release on failure, full audit trail
- **Live updates** — WebSocket push of job progress and completion
- **Export renderer** — the server-side pipeline that bakes the timeline into a file
- **Storage lifecycle** — where media lives, what gets cleaned up and when

---

## 4. What phase 1 does not include

Named explicitly so nobody has to guess.

| Not in phase 1 | Lands in |
|---|---|
| Face mapping, lip sync, any facial data at all | Phase 2 |
| GPU inference cluster | Phase 2 |
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
10. Export a 1080p 9:16 MP4 and download it
11. See their credit balance go down by the right amount, and go back up when a job fails

If any step needs an explanation, it is not done.

---

## 7. Who builds what

Workstreams that can run in parallel once [`05-api-contract.md`](05-api-contract.md) is agreed. The contract is what lets these proceed without waiting on each other.

| Workstream | Owns | Depends on |
|---|---|---|
| **Backend — core** | Accounts, projects, timeline persistence, credit ledger | — |
| **Backend — media** | Upload, probe, proxy/thumbnail/peaks generation, storage lifecycle | Core (accounts) |
| **Backend — jobs** | Queue, workers, progress, WebSocket, the three tool workers | Core, Media |
| **Backend — render** | Export pipeline: timeline document → finished file | Media, and the timeline document schema |
| **Frontend** | The whole editor: timeline UI, playback engine, tool integration, account screens | The API contract, not the backend itself |

**The frontend does not wait for the backend.** With the contract agreed, the editor is built against mocked responses and connected later. Most of phase 1's frontend work — timeline, playback, editing, undo — touches no server at all.

---

## 8. Blocking

| | What | Blocks |
|---|---|---|
| **A** | **Confirm or change the three AI tools above.** | Which worker gets written first, and whether phase 1 needs GPU hardware |
| **B** | Choose a payment provider and credit pricing. | Charging anyone. Everything else can be built and tested with manually granted credits. |
| **C** | Smart Trim: tighten a recording, or cut it to its best parts? | Whether Smart Trim is a phase 1 tool or a phase 3 one |

Decision A is the only one that blocks starting. B and C block finishing.

---

*AI Video Editor · Phase 1 Scope v1.0 · 12 August 2026*
