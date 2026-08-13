# Frontend Architecture

**How the editor is built: state, playback, timeline rendering, persistence and tool integration.**

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 12 August 2026 |
| **Audience** | Frontend engineers |
| **Depends on** | [`02-scope-v1.md`](02-scope-v1.md) · [`05-api-contract.md`](05-api-contract.md) |
| **Platform** | Web only in phase 1 ([`01-product-vision.md`](01-product-vision.md) §4.4) |
| **Proposed stack** | React 19 · TypeScript · Vite · Zustand + Immer · TanStack Query · WebGL2 |

> The stack is a proposal, not a constraint from anywhere else. What is **not** negotiable is the shape: an in-memory timeline document that the client owns, a compositing playback engine, and undo built on patches. Those follow from the product, not from React.

---

## 1. What this application actually is

Most web apps are forms over a database. This one is not, and treating it like one will fail in a specific and predictable way.

An editor is a **document application with a real-time renderer bolted to it**. The user manipulates a document at 60 frames per second while video decodes in the background. Two consequences run through everything below:

1. **The client owns the document.** The server stores it and validates it, but never computes edits. Dragging a clip is not a network call. ([`03-backend-architecture.md`](03-backend-architecture.md) §4.1 explains why the backend is built to allow this.)
2. **Playback is not a `<video>` tag.** A timeline is many clips from many files with effects and transitions. Nothing in the browser plays that natively. We build a compositor. §4 is the hardest part of this project.

---

## 2. Structure

```
src/
  app/                    routing, providers, auth guard
  editor/
    state/                the timeline document, commands, undo, selectors
    playback/             clock, video pool, WebGL compositor, audio graph
    timeline/             the track UI, ruler, playhead, waveforms, drag behaviour
    inspector/            per-clip property panels
    tools/                AI tool invocation, progress, result application
    export/               export dialog, progress, download
  media/                  upload, media bin, ingest status
  projects/               project list, create, duplicate, delete
  account/                sign in, register, credits
  api/                    generated client, WebSocket, TanStack Query hooks
  ui/                     buttons, dialogs, primitives
```

### Two kinds of state, kept apart

| | Server state | Editor state |
|---|---|---|
| What | Projects list, assets, jobs, credits, user | The open project's timeline, selection, playhead, zoom |
| Owner | **TanStack Query** | **Zustand + Immer** |
| Lifetime | Cached, refetched, invalidated | Lives while a project is open |
| Changes | When the server says so | Constantly, at interaction rate |

Mixing them is the most common way this kind of app goes wrong: the timeline ends up in a query cache and every clip drag triggers a refetch. **The timeline is never server state.** It is fetched once when a project opens and pushed back by autosave.

---

## 3. The timeline document and undo

### 3.1 One shape, everywhere

The in-memory timeline is **the same shape as the API's timeline document** ([`05-api-contract.md`](05-api-contract.md) §4). No client-side variant, no mapping layer. Autosave is `JSON.stringify` of what is already in the store.

Deriving anything the renderer needs but the document does not carry — clip end times, track durations, what is under the playhead — happens in memoised selectors, never as fields. A derived field stored in the document is a field that can be wrong.

```ts
interface EditorState {
  projectId: string
  version: number              // last version the server acknowledged
  timeline: TimelineDocument   // exactly the API shape
  selection: { clipIds: string[] }
  playhead: number             // ms
  zoom: number                 // pixels per second
  isDirty: boolean
}
```

### 3.2 Undo via patches, not hand-written inverses

Every edit goes through one function. Immer records what changed and how to reverse it.

```ts
import { produceWithPatches, applyPatches, type Patch } from 'immer'

interface HistoryEntry { label: string; patches: Patch[]; inverse: Patch[] }

function commit(label: string, recipe: (draft: TimelineDocument) => void) {
  const state = useEditor.getState()
  const [next, patches, inverse] = produceWithPatches(state.timeline, recipe)
  if (patches.length === 0) return

  useEditor.setState({
    timeline: next,
    undoStack: [...state.undoStack, { label, patches, inverse }].slice(-200),
    redoStack: [],
    isDirty: true,
  })
}

// Splitting a clip, moving one, and applying 1800 captions all use this.
commit('Split clip', draft => { /* mutate freely */ })
```

Undo pops an entry and applies its `inverse`. Redo reapplies `patches`.

**Why this and not a `Command` class per operation.** Phase 1 has roughly twenty distinct edits, each of which would need a hand-written `undo()`. Every one of those is a chance to write an inverse that is subtly wrong — and a wrong inverse corrupts a user's project in a way they discover much later. Immer derives the inverse from what actually changed. The whole undo system is the forty lines above.

**Grouping matters as much as correctness.** A Smart Trim result may remove thirty segments. Inside one `commit`, that is **one** undo step — which is exactly what the user expects when they press Ctrl+Z after running an AI tool. This is the single reason `commit` takes a whole recipe rather than being called per mutation.

`label` drives the tooltip: "Undo Split clip", "Undo Apply captions".

### 3.3 Not every interaction is a commit

A clip drag fires on every pointer move. Committing each one would push hundreds of entries onto the undo stack and re-render the tree at pointer rate.

**Drags use local component state and commit once, on drop.** The dragged clip renders at a transient offset; the store is untouched until the pointer is released. Same for trim handles, volume sliders and the grade strength slider — live feedback locally, one commit at the end.

---

## 4. The playback engine

The hard part. Budget for it accordingly.

### 4.1 What has to happen

At any instant the engine must show: the frame of the clip under the playhead, cropped and transformed, with its colour grade applied, blended with the neighbouring clip if a transition is in progress, with text overlays composited on top, while audio from the video and the music track plays in sync.

No browser API does this. We assemble it from four parts.

```
┌── master clock ──────────────────────────────────────────────┐
│  timeline position in ms; driven by the playing video's own  │
│  currentTime so audio stays in sync                          │
└────────────┬─────────────────────────────────────────────────┘
             │
   ┌─────────▼──────────┐    ┌────────────────────┐    ┌──────────────┐
   │  video element     │    │  WebGL compositor  │    │  Web Audio   │
   │  pool (3 hidden    │───►│  crop, transform,  │    │  graph       │
   │  <video> on        │    │  LUT, transition   │    │  volume,     │
   │  proxy media)      │    │  → <canvas>        │    │  fades, mix  │
   └────────────────────┘    └─────────┬──────────┘    └──────────────┘
                                       │
                             ┌─────────▼──────────┐
                             │  2D canvas overlay │
                             │  text and captions │
                             └────────────────────┘
```

### 4.2 The video element pool

Three hidden `<video>` elements, recycled. At any moment:

- **A** — the clip currently playing
- **B** — the next clip, already loaded and seeked to its `sourceInMs`, paused on the right frame
- **C** — spare, for scrubbing away from the playhead or for the far side of a transition

Preloading is the whole trick. When the playhead comes within ~2 seconds of a clip boundary, B loads the next proxy, seeks, and does a `play()` immediately followed by `pause()` — the browser will not decode and present a frame otherwise, and without that you get a black flash at every cut.

At the boundary the compositor switches which element it samples. Elements swap roles; nothing is created or destroyed. Creating a `<video>` per clip exhausts the browser's decoder budget within a couple of dozen clips.

All of this runs on **proxy media** (480p H.264, produced at ingest — [`03-backend-architecture.md`](03-backend-architecture.md) §6.2). Original 4K files do not scrub in a browser.

### 4.3 The clock

Do not drive playback from `performance.now()`. It drifts against decoded audio, and the drift is audible within a minute.

**The currently playing video element is the clock.** Its `currentTime` is what its own audio is synced to, so following it means never resyncing.

```ts
function timelinePosition(clip: MediaClip, videoTimeMs: number): number {
  return clip.startMs + (videoTimeMs - clip.sourceInMs) / clip.speed
}
```

When no video is under the playhead — a gap, or a text-only stretch — fall back to a `performance.now()` clock until the next clip takes over.

Use **`requestVideoFrameCallback`** rather than `requestAnimationFrame` for the compositor loop wherever it is available. It fires once per *decoded video frame* with a precise media timestamp, which is what frame-accurate work needs; `requestAnimationFrame` fires per *display* refresh and will happily show you the same frame twice or skip one. Keep a `requestAnimationFrame` fallback for gaps and for browsers without it.

### 4.4 The compositor

One WebGL2 canvas at project resolution, scaled by CSS to fit the preview area.

| Effect | How |
|---|---|
| **Crop and transform** | Vertex shader. `transform.crop` selects source UVs; scale, offset, rotation and flips are a matrix |
| **Colour grade** | Fragment shader sampling a 33×33×33 LUT as a `TEXTURE_3D`, mixed against the ungraded colour by `strength` |
| **Transitions** | Sample both clips' textures and blend by transition progress. This is why the pool needs a third element |
| **Text** | Separate 2D canvas layered over the WebGL canvas. Text is cheap and rarely changes; putting it in the shader buys nothing |

> **The LUT files are shared with the export renderer, and the strength maths must match.** If the browser and FFmpeg apply the same grade differently, users see one picture while editing and a different one after export — reported as a bug that is genuinely hard to trace. Same `.cube` files, same formula, and a fixture test comparing a rendered frame against the browser's output on both sides.

**All spatial values are normalised 0–1** ([`05-api-contract.md`](05-api-contract.md) §4.3). The compositor multiplies by the canvas size at draw time. This is what makes a caption at `y: 0.78` land identically in a 480p preview and a 1080p export.

### 4.5 Audio

A Web Audio graph, not `<audio>` elements:

```
video A ──► MediaElementSource ──► GainNode (clip volume × fades) ──┐
music    ──► MediaElementSource ──► GainNode (clip volume × fades) ──┼──► destination
```

Per-clip volume and fades are gain automation scheduled against the audio context clock, so they are sample-accurate rather than being stepped from a rAF loop.

### 4.6 Scrubbing

Scrubbing is not playback and must not be treated as one.

- Dragging the playhead: seek the relevant element, draw one frame, do not start playback.
- Throttle seeks to ~15 per second. Video seeks are expensive and queueing them makes the UI feel worse, not better.
- Seek to the last requested position when the drag settles, so the final frame is exact even if intermediate seeks were dropped.
- Crossing a clip boundary while scrubbing swaps which pool element is sampled, exactly as during playback.

---

## 5. The timeline UI

### Virtualise by time, not by row

A one-hour podcast at a usable zoom is far wider than any viewport. Render only what is inside the visible time window, plus a screen's worth either side. A project with 500 clips must cost the same to render as one with 5.

### Waveforms are canvas, never DOM

Peaks come from the API as an array of 0–1 amplitudes at 100 buckets per second ([`05-api-contract.md`](05-api-contract.md) §3). Draw them to a `<canvas>` per clip, downsampled to the clip's current pixel width. Fetch once per asset, cache in memory — a 10-minute file is ~60 000 numbers.

Redraw on zoom change, debounced. Never one DOM node per peak.

### Interaction

- **Snapping** to clip edges, the playhead and zero, within ~8 pixels, with a modifier to suppress it
- **Selection**: click, shift-click for range, marquee
- **Keyboard**: space to play, `S` to split at the playhead, arrows to nudge by one frame, Ctrl+Z / Ctrl+Shift+Z
- **Drop targets** are the track lanes; a drag that would overlap another clip shows a rejection state rather than silently snapping somewhere unexpected (invariant 1 in the API contract)

---

## 6. Persistence

### Autosave

Debounced 2 seconds after the last commit, plus immediately on tab blur and on `visibilitychange` to hidden.

```
PATCH /v1/projects/{id}   { timeline, version }
  → 200 { version: 13 }   store the new version, clear isDirty
  → 409 VERSION_CONFLICT  §6.1
```

Rules that matter:

- **Never autosave mid-drag.** Save on committed state only.
- **One save in flight at a time.** If edits arrive while a save is running, mark dirty and save again when it returns. Overlapping saves race on `version` and produce spurious conflicts.
- Use `navigator.sendBeacon` on `pagehide` as a last-chance flush.

### 6.1 Version conflicts

A 409 means the same user changed this project somewhere else — a second tab, or another device. It should be rare, and when it happens it is not merge-able: two timelines that diverged have no correct automatic reconciliation, and inventing one silently destroys work.

**Show the user the choice.** A dialog: *"This project was changed in another tab."* — **Keep my version** (re-`GET` for the current version number, re-`PATCH` with our timeline) or **Load the other version** (discard local state, reload, clear undo history).

Attempting an automatic merge is the wrong answer here. The right answer is a rare, honest dialog.

### 6.2 Recovering from a crash

Mirror the timeline into IndexedDB on every commit, keyed by project and version. On open, if a local copy is newer than the server's, offer to restore it. Cheap to build, and it is the difference between a browser crash costing five seconds or an afternoon.

---

## 7. AI tools in the editor

The whole point of the editor model is that an AI result is **an edit like any other**. It arrives, it is undoable, it is adjustable.

### The flow

1. **Price it first.** `POST /jobs/estimate` when the tool panel opens, so the button reads "Add captions — 22 credits" and never surprises anyone.
2. **Invoke.** `POST /jobs` with an `Idempotency-Key`. Show the affected clip as busy — a badge on the clip itself, not a modal. The user keeps editing.
3. **Watch.** WebSocket `job.progress` updates the badge. If the socket is closed, poll `GET /jobs/{id}` every 3 seconds. Both paths, always — the socket is an optimisation ([`05-api-contract.md`](05-api-contract.md) §7).
4. **Apply.** On `job.succeeded`, `GET /jobs/{id}` (following `resultUrl` when the payload is large) and apply inside **one** `commit`.
5. **Handle failure.** Map `errorCode` to a sentence and offer to retry. Credits came back automatically — say so, because otherwise the user assumes they lost them.

### Applying each result

```ts
// Captions → one text clip per word, in a single undo step
commit('Add captions', draft => {
  const track = ensureTextTrack(draft)
  track.clips = result.words.map((w, i) => ({
    id: `clp_cap_${jobId}_${i}`,
    kind: 'caption',
    startMs: clip.startMs + (w.s - clip.sourceInMs) / clip.speed,
    durationMs: (w.e - w.s) / clip.speed,
    text: w.w,
    styleId: chosenStyle,
    position: { x: 0.5, y: 0.78, anchor: 'center' },
    emphasis: w.em,
    sourceJobId: jobId,
  }))
})
```

- **Smart trim** — split the clip at each removal boundary and drop the removed segments, then close the gaps. One `commit`. Every resulting cut is an ordinary draggable edit.
- **Colour grade** — write one `effects` entry on the clip. The compositor picks it up on the next frame; nothing else happens. This is the tool that shows why grading became an analysis job: the result is four numbers and the picture changes instantly.

**Results are always translated into timeline time.** Job results are in *asset* time (§6.2 of the contract); a clip has a `sourceInMs` and a `speed`. Getting this conversion wrong puts captions slightly out of sync in a way that looks like a transcription problem, so it is worth a unit test with a trimmed, sped-up clip.

**`sourceJobId` on generated clips earns its place**: it lets the inspector say "from Captions", offer "re-run", and select everything a given job produced so it can be removed in one action.

---

## 8. Performance budget

Numbers to hold the build against, not aspirations.

| | Target |
|---|---|
| Playback | 60 fps sustained, no dropped frames at 1080p preview |
| Scrub → frame on screen | < 100 ms |
| Clip drag | No frame over 16 ms |
| Project open → editable | < 2 s on a 50-clip project |
| Timeline responsive up to | 500 clips |
| Undo/redo | < 50 ms |
| Initial bundle | < 400 KB gzipped, editor code split out |

Where the time actually goes, in order: video decode, WebGL draw, React re-renders. Only the third is ours to lose cheaply — subscribe to narrow slices of the store, memoise selectors, and keep the compositor loop entirely outside React. **The render loop must never call `setState`.** It reads from the store imperatively and draws.

---

## 9. Testing

| Layer | Approach |
|---|---|
| Timeline operations | Pure functions over documents. Split, trim, move, delete — property-based tests asserting the §4.3 invariants hold after any sequence of operations |
| Undo/redo | Apply N random operations, undo N times, assert the document equals the original. This catches inverse bugs that no example test will |
| Result application | Fixtures for each tool, including a trimmed and sped-up clip, to pin the asset-time to timeline-time conversion |
| Compositor | Render fixture frames to a canvas and compare against reference images, including one grade at several strengths, shared with the backend renderer |
| Playback | Manual against a checklist: clip boundaries, transitions, seek during play, tab backgrounded, slow network |
| API integration | Against the mock server from the OpenAPI schema ([`05-api-contract.md`](05-api-contract.md) §10) |

---

## 10. Sequencing

Ordered so the riskiest work is proven earliest, and so nothing waits on the backend.

1. **Playback spike, first.** Two clips, a cut between them, a LUT, a text overlay — hardcoded, no UI, no state management. This proves the hardest part is achievable in a browser before anything is built on top of it. If it does not work, everything else changes.
2. **Timeline document + commands + undo**, headless, with tests.
3. **Timeline UI** — tracks, clips, playhead, zoom, waveforms.
4. **Editing interactions** — drag, trim, split, snapping, selection, keyboard.
5. **Wire the compositor to the document.**
6. **Media** — upload, bin, ingest status. First backend dependency.
7. **Persistence** — autosave, conflicts, crash recovery.
8. **AI tools** — panels, progress, result application.
9. **Export** — dialog, progress, download.
10. **Account and credits** — sign in, balance, history.

Steps 1–5 touch no server at all. Steps 6 onward run against the mock server until the endpoints land.

---

## 11. Risks

| Risk | Why it matters | What we do |
|---|---|---|
| **The compositor is harder than estimated** | It is the one part with no library to fall back on and no way to descope | Spike it first (§10.1). If it slips, the whole schedule moves — better known in week one than month three |
| **Safari** | Different codec support, `requestVideoFrameCallback` history, autoplay rules, WebGL quirks | Test on Safari from the first spike, not at the end. Proxies are H.264 specifically because Safari is reliable with it |
| **Browser decoder limits** | Too many simultaneous `<video>` elements silently stop decoding | The three-element pool (§4.2) is the mitigation, and the reason it is not one element per clip |
| **Memory on long projects** | Peaks, thumbnails and textures accumulate | Cap caches by total bytes, evict by distance from the playhead |
| **Grade mismatch with export** | Users see one picture, get another | Shared LUT files and a shared frame-comparison fixture on both sides (§4.4) |
| **Timeline document schema drift** | The frontend and renderer disagree about a field | Types generated from the OpenAPI schema; the contract is the single source |

---

*AI Video Editor · Frontend Architecture v1.0 · 12 August 2026*
