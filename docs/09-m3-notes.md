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
- **The IndexedDB mirror** 💤 — already marked deferrable, and the proper answer
  to the 64 KB unload cap above.
