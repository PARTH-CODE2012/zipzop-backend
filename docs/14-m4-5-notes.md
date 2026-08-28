# M4.5 build notes — the interface pass

**What the seven items turned into, the three defects found along the way, and the
one thing that could only be found by opening the editor.**

| | |
|---|---|
| **Milestone** | M4.5 — *"the editor can be reached, driven and understood by someone who did not build it"* |
| **Date** | 25 August 2026 |
| **Read it after** | [`12-m4-5-interface-pass.md`](12-m4-5-interface-pass.md), which this implements item for item |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M4.5 |
| **Gates** | **304 tests** (was 236) · `tsc --noEmit` clean · `eslint` clean · production build green |

Driven in a real browser on Windows, against the fixture server rather than the
backend — see §5 for exactly what that does and does not prove.

---

## 1. What changed structurally

Two of the seven items were layout. One was a genuine structural change, and it
is the one worth reading.

### The rail is not a nicer panel — it is a different growth curve

The editor had **two components stacked on the right**: an inspector with a fixed
height and a tools panel with a fixed width. Three AI tools filled it. Phase 2
adds four more — face mapping, lip sync, denoise, dereverb — and the honest
question the original document asked was where the seventh one goes.

Stacking a third block answers that for one release. The rail answers it for the
product: **it grows by one icon per tool, and the inspector's job never changes
shape.** Adding a phase 2 tool is one entry in
[`rail/modes.ts`](../frontend/src/editor/rail/modes.ts) and one panel — no
relayout, no renegotiating what the right-hand column is for.

That is also what created somewhere for item 3's missing manual controls to live.
The two items were the same problem seen from two sides: the toolbar was thin
because there was nowhere to put anything, and the panels did not scale because
everything had been put in them. Putting a colour picker in the toolbar would
have made the toolbar the thing that does not scale.

### What moved, and what deliberately did not

| | |
|---|---|
| **Transport** | Header → under the picture. Same store actions, same shortcuts; the keyboard path in `keyboard.ts` is untouched, so there is one behaviour with two ways in rather than two implementations to keep in agreement |
| **Volume, speed, fades** | Inspector → the rail's Audio mode. The inspector answers *what is this clip*; these answer *how does it sound* |
| **The three AI tools** | Right-hand panel → three rail modes. `ToolsPanel.tsx` is deleted |
| **The WebSocket** | `ToolsPanel` → the workspace. It used to be opened by the panel that showed the tools, which was fine while they were one component and wrong the moment they became modes: a socket that opens on Captions and closes on Media would drop the progress of the job you just started |
| **Zoom behaviour** | 🟢 **Unchanged**, per 22 August. Plain wheel still scrolls. Only the discoverability was ever the problem |
| **Any new effect type** | 🟢 **Not built**, per 22 August. An effect the browser can draw and FFmpeg cannot is a preview that disagrees with the exported file |

---

## 2. Three defects found along the way, and one inherited

### 🔴 Typing `42` into a percentage field set it to 100 %

**The worst kind of bug this pass could have produced**, because it was in the
control added *specifically* so that values could be set exactly.

Strength is stored `0–1` and displayed as a percentage. The field showed `66 %`.
Typing `42` — which is what anybody reading a percentage types — parsed as 42,
clamped against a maximum of 1, and wrote **full strength**. No error, no
rejection, no sign anything had happened other than the picture changing more
than asked.

The cause is worth naming precisely: the field's **displayed unit and its stored
unit had diverged**, and nothing in the type system or the tests could see it,
because both were `number`.

The fix is an explicit `scale`, with the rule that **`min`, `max` and `step` are
always in the document's unit and everything the user sees or types is in the
displayed one**. `toDisplay` and `toDocument` are pure functions in
[`number-field.ts`](../frontend/src/editor/controls/number-field.ts) rather than
arithmetic inside the component — the same reason every other constraint in this
codebase sits outside the component that happens to be the way in today.

Writing the round-trip test then found a **second** defect in the fix: `0.07 ×
100` is `7.000000000000001`, which reaches the range input as a `value` against a
`step` of `1`. So the rounding had to happen on the way *out* as well as in. The
test walks all 101 steps of a percentage range and asserts an exact round trip.

### The toolbar pointed at a panel that no longer existed

After the tools moved to the left rail, the toolbar still read *"AI tools are in
the panel on the right"*. Every test passed. It is a one-line fix and it is only
findable by opening the editor — which is the entire thesis of the document this
milestone implements.

### 🔴 Playback stopped at the last caption instead of the end of the timeline

Reported after the pass shipped, and it is an older defect than M4.5 — but it is
the same shape as the two above, so it belongs with them.

**What happened.** The picture looped back to the start at the end of the last
caption word rather than running to the end of the project. The ruler, the
transport and `selectDurationMs` all read the real length; the engine did not.

**Why.** [`timeline-adapter.ts`](../frontend/src/editor/playback/timeline-adapter.ts)
measured the playable timeline by reducing over the clips it had just built —
`video` and `text`. That was wrong twice:

* **the audio track was never in the sum**, so a music bed running past the last
  frame was cut off mid-note;
* **clips dropped for having no proxy took their length with them.** A clip whose
  asset is still ingesting is deliberately left out of `video` — handing the
  engine an empty `src` makes the playhead hold forever waiting for a frame that
  is never coming. But dropping it from the *measurement* too meant a project
  where nothing had finished ingesting measured only its captions, and looped at
  the last word.

**The fix is to stop measuring.** The length now comes from
`timelineDurationMs(document)` — the same function the ruler and the transport
use. There is one answer to *how long is this project*, and playback is not
entitled to a different one. A clip still being ingested does not shorten the
project; it appears when it is ready.

Two tests, both failing against the old code: one for the audio track (`9000`
instead of `15000`), one reproducing the report exactly (`6800` instead of
`15000`).

> **Not verified in a browser.** `requestAnimationFrame` is suspended in a tab
> the compositor is not painting, so the engine loop cannot run in the harness
> used here — `document.hidden` is `true` and rAF fires zero times. The defect
> and the fix are both at the adapter, which is pure and tested; what has *not*
> been watched is the picture actually playing to the end.

### An inherited one, found while verifying

`undo()` opens with `if (state.drag) return` — *"a gesture in flight owns the
document's shape"*. That is correct, and it means **a drag left in flight
silently disables undo**. The real editor always ends its drags, so this is not a
live defect; but any path that ends a gesture without `endDrag` would produce
*"undo does nothing"*, with no error and no clue. Recorded here rather than
changed, because the guard is right and the fix would be to make losing a drag
impossible rather than to weaken it.

> **Method note.** This was found by driving the page with synthetic pointer
> events, which is also what made it *look* like a bug: `setPointerCapture`
> throws on a synthetic pointer, aborting the clip's handler mid-way and leaving
> exactly the half-started drag described above. The first reading — *"undo is
> broken"* — was wrong, and the difference between the two was one more check
> rather than one more assumption. **Verify what the harness did before believing
> what it reported.**

---

## 3. What was built

| | |
|---|---|
| [`rail/modes.ts`](../frontend/src/editor/rail/modes.ts) | The mode list, as data. No React |
| [`rail/ModeRail.tsx`](../frontend/src/editor/rail/ModeRail.tsx) · [`rail/panels.tsx`](../frontend/src/editor/rail/panels.tsx) | The rail, and what each mode shows |
| [`rail/rail-store.ts`](../frontend/src/editor/rail/rail-store.ts) | Which mode is open. **Its own store, not the editor's** — nothing here commits, undoes, or may ever reach a patch |
| [`controls/number-field.ts`](../frontend/src/editor/controls/number-field.ts) + `.tsx` | Parsing, snapping, display units; the compact field over them |
| [`transport/Transport.tsx`](../frontend/src/editor/transport/Transport.tsx) | Five controls, four of which had no button before |
| [`layout/split.ts`](../frontend/src/editor/layout/split.ts) + `TimelineSplitter.tsx` | The divider's bounds and its gesture |
| [`app/projects/projects-client.tsx`](../frontend/src/app/projects/projects-client.tsx) | The list that replaces the stub |
| `clearColorGrade` in `operations.ts` | New. A picker needs a "None", and the only way back from a grade was undo — a poor fit for *"I tried five looks and want none of them"* |

**`ToolsPanel.tsx` is deleted.** Its three rules survive in the rail: the price is
on the button before the click, editing continues while a job runs, and the
result is an ordinary undoable edit.

### The divider's one interesting rule

`clampTimelineHeight` takes the viewport height, not a constant, because 320 px
of timeline is comfortable on a monitor and the whole window on a laptop in split
screen. When the two minimums do not both fit, **the timeline gives and the
picture stays** — an editor with no preview and a divider you can no longer see
to drag back is a state with no way out.

---

## 4. `NaN` and `Infinity` are not the same kind of broken

A small decision, recorded because the first version got it wrong and a test
caught it. `clampTimelineHeight(NaN)` returns the default: `NaN` has no order, so
there is nothing to clamp it to. `clampTimelineHeight(Infinity)` returns the
**ceiling**: an infinity *does* have an order, and a function called `clamp` that
refuses to clamp the largest possible number is surprising in a way that gets
worked around somewhere else.

---

## 5. How this was verified, and what that is worth

**Docker is not available on the machine this was built on**, so Postgres, Redis
and MinIO could not run, so the backend could not run. What made a browser check
possible anyway is
[`lib/api/fixtures.ts`](../frontend/src/lib/api/fixtures.ts) — **the mock server
`PHASE1-TASKS.md` has carried unticked since M0**, in the smallest form that
does the job: one branch inside `request()`, behind `NEXT_PUBLIC_DEMO=1`.

Not Prism and not MSW. Both are a dependency and a second process, and what was
actually needed was for one function to answer from a table.

**It is typed against `generated.ts`, and that immediately earned its place**: the
first four versions of the fixtures did not compile. `MeResponse` has no `plan`
field, `AssetResponse` calls it `originalFilename` and has no `hasAudio`,
`TextClip` requires `emphasis`, and `ProjectResponse` carries the canvas
dimensions. Every one of those would have been an interface built against a
server shape that does not exist.

Two guards, because a demo mode that can reach a deployment is a way to serve
invented data to a customer:

* off unless `NEXT_PUBLIC_DEMO=1`;
* `assertNotProduction()` **fails the production build outright** if the flag is
  set. Verified: `NEXT_PUBLIC_DEMO=1 pnpm build` exits 1 with the message.

### What was actually driven in the browser

Every rail mode opened and filled its panel · a grade applied by hand and the
strength typed · undo and redo across a delete (21 → 20 → 21 → 20) · the
transport's five controls present and absent from the header · the zoom hint on
screen · the divider carrying `role="separator"`, an accessible name and a
keyboard path · the project list rendering three projects with relative times ·
the inspector reduced to properties and pointing at where the audio controls went.

### What it does not prove — and this matters

Nothing here touches ffmpeg, S3, Postgres, Celery or a real job. **The fixture
server proves that screens render and that state flows; it proves nothing about
ingest, jobs, credits or persistence.** There is also no picture in the preview
on this machine — no ffmpeg means no proxy, and `proxyUrl` is `null` in the
fixtures on purpose rather than pointing at a URL that would 404 and look like a
renderer bug.

**The real proof remains [`e2e/m2.mjs`](../frontend/e2e/README.md)**, which drives
a real browser against real infrastructure and is the thing that has actually
caught bugs. M4.5 has not been through it. It should be, on a machine with
Docker, before M5 closes.

---

## 6. What is left

| | |
|---|---|
| ⚪ **The fixtures are partial** | A caption result with a misspelled name, a graded clip, an ingesting asset and two-bucket credits exist. A smart-trim result, a **failing job** and a free account hitting `PLAN_LIMIT_EXCEEDED` do not — and the failure states are the M4 item still open |
| ⚪ **M4.5 has not run through `make e2e`** | Needs Docker. Before M5 closes |
| ⚪ **The rail has no keyboard shortcut** | Six modes and no way to reach them without a pointer. `aria-current` and the labels are there; a `1`–`6` binding is not |
| 💤 **The two missing caption styles** | Unchanged from M4. Design work, not engineering |

Nothing here blocks M5.
