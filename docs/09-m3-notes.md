# M3 build notes — editing that survives a reload

**What M3 decided, what it broke, and what it deliberately left alone.**

| | |
|---|---|
| **Milestone** | M3 — *"you can cut, arrange, undo, close the tab, and come back to exactly what you left"* |
| **Dates** | 18 August 2026 |
| **Read it before** | starting M4 |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M3 |

Verified end to end on a running stack: register, create a project, put a title
on the text track, reload, and the title is still there at the version the
server assigned.

---

## 1. What was decided

### The timeline document is typed, field by field, on the server

[`04-frontend-architecture.md`](04-frontend-architecture.md) §3.1 asks for the
client's timeline type to be *generated* rather than hand-written. That only
works if the server's schema describes the document properly, so
`backend/app/api/schemas/project.py` spells out every field of §4 instead of
accepting `dict[str, Any]`. Anything left loose there arrives in the frontend as
`Record<string, unknown>` and the two sides drift on the first field anyone adds.

One consequence worth knowing before adding a field: **`Field(default=[])`, not
`default_factory=list`.** A factory cannot be represented in a JSON schema, so
the field emits no `default`, openapi-typescript marks it optional, and every
read site in the client needs a `?? []` — which is the fresh-array-per-render
bug M2 spent an end-to-end run finding. Pydantic deep-copies mutable defaults,
so the list is not shared between instances.

### Undo is patches, and a commit is one step however large

`commit(label, recipe)` runs the recipe through `produceWithPatches` and pushes
the inverse. The property that matters is not the undo itself but its
granularity: a commit that changed eighteen hundred clips is **one** undo step,
which is what M4 needs when a caption run lands a clip per word.

A recipe that changes nothing records nothing — a drag that ends where it
started, or a property set to the value it already had, produces no history
entry and no autosave.

### Autosave has four rules and each has a failure behind it

Debounced two seconds · one save in flight at a time · never mid-drag · a `409`
stops the loop until the user chooses. The whole state machine is a plain class
in `editor/state/autosave.ts` taking its dependencies as arguments, so all four
are tested on fake timers with no network and no React. None of them would show
up in a click-through.

### Snapping is measured in pixels

A tolerance in milliseconds is a fifth of a pixel when zoomed out and half the
screen when zoomed in. What a hand judges is distance on screen, so that is the
unit. `alt` suppresses it — the only way to place a clip one frame off an edge
it wants to stick to.

### The timeline is virtualised by time, not by row

It is one row deep per track and thousands of clips wide, so windowing rows
would save nothing. Clips outside the visible window plus half a screen of
overscan are not rendered, and the grid is a repeating background rather than
one element per line.

---

## 2. Three defects, and the gap that hid two of them

Written up in full in [`06-m2-notes.md`](06-m2-notes.md) §2b. In short:

**The test harness never exercised the request transaction boundary.** The
`client` fixture overrode `get_session` with a bare `yield db` — no commit, no
rollback — so any handler that wrote and then raised looked correct to the
suite. Restoring the real semantics made an existing, green test fail.

- 🔴 **The refresh-token chain was never actually revoked.** `auth.refresh`
  called `revoke_all_for_user()` and then raised a 401, which rolled the
  revocation back. The branch that exists to kill a leaked session was the one
  event guaranteed to undo itself.
- **A rejected upload kept its reservation**, at its *announced* size, while
  storage held whatever was really uploaded — the quota evasion the size check
  exists to prevent.
- 🔁 **The M2 render loop, reintroduced.** `selectLanes` sorted and
  `selectAllClips` flat-mapped, so both built a fresh array on every read.
  Zustand compares by reference, so the value was never equal to its previous
  self: render, re-read, render — `Maximum update depth exceeded` and
  `The result of getSnapshot should be cached to avoid an infinite loop`, and a
  blank editor. `selectClipBoundsMs` had it too, by returning a fresh object.
  **Both selectors are perfectly correct in isolation**, which is exactly why
  M2's unit suite missed it and M3's did too — the fault only exists once React
  subscribes.

  The fix is a `WeakMap` keyed on the timeline document, which changes precisely
  when the answer does, and demoting the bounds helper out of the `select*`
  namespace so nobody subscribes to it. The guard is
  `selector stability` in `store.test.ts`: it asserts identity rather than
  values, which is the property Zustand actually needs and the one no ordinary
  assertion checks.

- **`markSaved` always cleared the dirty flag.** `state.timeline !== get().timeline`
  inside a Zustand `set()` callback compares the same object. An edit made while
  a save was in flight was stranded: nothing sent it until the next change, and
  closing the tab in between lost it. The save's snapshot is now passed back and
  compared by reference.

---

## 3. Two corrections to the agreed documents

**`sendBeacon` cannot flush the timeline.** [`04-frontend-architecture.md`](04-frontend-architecture.md)
§6 prescribed it for `pagehide`; it only ever issues a POST and the save is a
PATCH. `fetch(..., { keepalive: true })` does the job, with a 64 KB body cap that
makes it best-effort rather than a safety net — a caption-heavy timeline exceeds
it. The debounce and the `visibilitychange` flush are the real protection, and
the deferred IndexedDB mirror is the proper answer.

**The A2 mockup's label colour failed contrast.** `#61616E` measures 3.2:1,
which fails for the 11 px labels it was used on. [`08-ui-charter.md`](08-ui-charter.md)
§3.2 replaces it with `#7A7A87` at 4.6:1 and states that where a mockup and the
charter disagree, the charter wins.

---

## 4. The local dev flow, which did not work

Two problems, both hit the moment anyone tried to use the running application
rather than the test suite:

1. **The dev database had no schema.** `alembic upgrade head` had only ever been
   run against `zipzop_test`, by the pytest fixture. Registering an account
   against a running stack returned a 500 from the first query.
2. **`make dev-all` started the servers without checking either.** It now
   depends on `infra` and `migrate`, both idempotent, so it cannot start against
   dead containers or an unmigrated database.

A third thing made both harder to diagnose than they should have been: the
sign-in panel rendered *"Something went wrong. Try again."* for a `fetch` that
never reached the API at all. That is the one case where trying again cannot
help, so it now names the address it could not reach.

---

## 5. Deliberately not done in M3

- **The text track's font, size and colour.** Adding, editing and positioning a
  title work. The `style` override object is in the document and generated into
  the client types, so each of the three is a control rather than a change of
  shape.
- **Drag-to-crop.** Reframing is two presets. The operation accepts any
  normalised rectangle; only the handle to draw one is missing.
- **Web Audio gain automation.** Per-clip volume and fades are in the document
  and the export renderer will honour them; the browser preview does not apply
  them yet.
- **Transitions in the preview.** They are editable, clamped and saved, and the
  preview draws every join as a cut. This one was *not* a decision when M3
  shipped — it was an oversight the audit below found, and it stays open because
  closing it is not an adapter change. The engine derives a crossfade from two
  clips **overlapping in time**, and the document forbids that (invariant 1): a
  transition is metadata on a join. Turning it into an overlap means deciding
  which side gives up the frames, and **the contract does not say** — §4.2 only
  records that a transition *"overlaps the neighbouring clip"*. Whatever is
  decided, the export renderer in M5 has to make the same choice, so it belongs
  in `05-api-contract.md` before it belongs in code.
- **The IndexedDB mirror** 💤 — already marked deferrable, and the proper answer
  to the 64 KB unload cap above.

---

## 6. The audit after M3, and what a second read found

M3 shipped with 182 green tests and a working end-to-end run. Reading it again
found nine defects anyway, and the pattern in them is worth more than the list:
**almost every one lives at a seam that no single module owns.**

### The two that lost the user's work

Both end the same way — the autosave comes back `422 INVALID_TIMELINE`, the
status bar says "Could not save", the loop stops, and everything since the last
good save is gone on reload. Neither names the clip on screen.

- 🔴 **A trim-end drag could read past the end of its media.** `trimEnd` has
  taken a `maxSourceMs` bound since it was written, it is tested, and **nothing
  in the interface ever passed it**: `endDrag` called the two-argument form. The
  only ceiling was the next clip, so the very first thing a user does — add a
  clip, pull its right edge out — produced a document the server rejects on
  invariant 4. The asset's length is not in the document (nothing derivable is),
  so the store now holds it separately, fed from `GET /media`, and both the
  commit and the drag preview clamp to it.
- 🔴 **A transition was clamped when it was set, and never again.** Invariant 7
  is a bound on *two clip durations*, so trimming, splitting, moving or deleting
  a clip can put a transition over the line without touching it. `clampTransitions`
  now runs after every operation that changes a duration or a neighbour — one
  pass, writing only where the value actually changes, so a no-op produces no
  Immer patch and no spurious undo step.

### The seams

- **Titles never reached the screen.** The adapter handed the engine `text: []`
  unconditionally. The engine has drawn text since M1 and the document has
  carried it since M3; the join between them was the one place nobody looked.
  And once wired, the overlay's dirty check keyed on clip **ids** — correct for
  captions, which are generated once, and wrong for the one thing M3 added: a
  title exists to be retyped, and retyping it would not have redrawn.
- **`keepMine` and `loadTheirs` used the route segment, not the project.**
  `/editor/scratch` creates a project and swaps the id in with `replaceState`,
  which does not re-render the route — so the prop still read `scratch` for the
  rest of the session. Resolving a conflict therefore 404'd, and "load the other
  version" created a *third* project instead of reloading. Both now ask the
  store what was actually opened.
- **Strict Mode created two projects.** `next.config.ts` keeps Strict Mode on
  deliberately and warns that effects run twice; the open effect was not written
  for it, so every visit to `/editor/scratch` left an orphaned empty project.

### The interface

- **A plain click on empty lane selected clips on other lanes.** The marquee had
  a time range and no vertical extent, so it was a band across every track at
  once. `marqueeSelection` now takes the lanes it covers; either half alone
  gives the wrong answer.
- **⌘/ctrl + wheel zoomed the browser as well as the timeline.** React attaches
  `wheel` to the root with `{ passive: true }`, so `preventDefault()` inside a
  JSX `onWheel` does nothing but log a warning. The listener is registered by
  hand now, where it can say it is not passive.
- **The grid did not move with the scroll.** A `repeating-linear-gradient`
  repeats from the element's own left edge while the ruler draws ticks at
  `tick.px - scrollPx`, so the two agreed only at the very start of the
  timeline.
- **Every inspector slider committed per pixel of travel.** One pull of the
  volume handle was forty undo steps and forty queued autosaves. The store's
  rule 2 — *a drag does not commit* — was written for clips and applies here
  unchanged: the number under the hand is local state, the release is the edit.

### And the one that hid the rest

**`make test-backend` did not run from a clean shell.** The fixture shelled out
to a bare `alembic`, and the Makefile runs `./.venv/bin/pytest` without
activating the environment — 129 errors before a single test executed, from the
command the README names as the gate before pushing. CI never saw it, because CI
installs into the runner's own Python where `alembic` resolves. `sys.executable
-m alembic` works in both. **A green CI badge is not evidence that the
developer's command works**, and the one people actually type is the one that
has to.

Also fixed, smaller: **`text-overlay.ts` was a binary file to git.** Its
"nothing drawn yet" sentinel was a *literal NUL byte* in the source, and one NUL
in the first 8 kB is all it takes — the file has never appeared in a diff, a
blame or a review since M1, silently. The same value written as `'\u0000'`
behaves identically and is six ASCII characters. `auth.refresh` cleared the
refresh cookie on three error paths and none of them worked — FastAPI copies the injected `Response`'s headers
onto the reply only when the handler *returns*, and all three raise, so the
browser kept a dead token for thirty days. And `scale.ts` and `operations.ts`
each carried a snapping implementation nothing imported; `gestures.ts` is the
one the timeline uses.
