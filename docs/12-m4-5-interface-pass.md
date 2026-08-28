# M4.5 — the interface pass

**Eight problems found by using the editor rather than testing it, two of them
resolved by the same decision.** Written 22 August 2026, after M4 closed and
before M5 starts; the three open scope decisions closed the same day.

| | |
|---|---|
| **Status** | ✅ **Shipped 25 August 2026.** All seven items — see [`14-m4-5-notes.md`](14-m4-5-notes.md) for what they became, the two defects the work introduced, and what the verification is and is not worth |
| **Why it exists** | Nothing in M0–M7 is a usability pass. The [UI Charter](08-ui-charter.md) settled the palette, the type scale and the component states; it never covered control density, layout or discoverability |
| **Depends on** | Nothing. Every item here is independent of M5 |
| **Blocks** | Nothing — but see *"When to do this"* below |

---

## Why this is not already in the plan

The eight milestones are all functional: foundations, playback, accounts,
editing, AI tools, export, payment, security. The only visual work scheduled was
the charter in M3, and it did exactly what it set out to do — one file of tokens,
applied without touching a component.

What the charter deliberately did **not** decide: how big a control should be,
where a control should live, how dense a panel should be, or whether a user can
find a feature at all. Those are the items below. They were found by opening the
editor and trying to use it, which is not something a test suite does.

> **Three scope questions closed 22 August**, the same day this document was
> written: keep the current mouse-wheel behaviour rather than change it (item 6);
> fold the two side panels into one rail-plus-inspector structure rather than
> leave them stacked (items 4 and 5, merged into one decision below); and ship
> manual controls only for what the renderer already implements this pass, with
> any new effect type held for Phase 2 (item 3). What is left open is not the
> design any more — it is *when* the work gets scheduled, which is the project
> lead's call.

> **One item from the same session is not in this document.** Titles and captions
> could not be moved, trimmed, split or duplicated at all — the lookup those
> operations went through searched video and audio tracks only. That was a
> functional defect rather than an interface one, and it was fixed on 22 August
> with nine tests that fail against the old code.

---

## When to do this

Two defensible orders, and the choice is a scheduling call rather than a
technical one.

**Before M5** — the editor's interface is fresh, and M5 adds only one screen (the
export dialog) on top of it. The cost is a delay to the first exported file,
which is the most visible remaining milestone.

**After M7, as one pass** — M5 and M6 each add interface of their own (export
dialog, pricing page, paywalls, balance displays). Doing one pass at the end
covers all of it at once instead of polishing a surface that is about to grow.
The cost is living with the problems below for three more milestones, and the
risk that "polish at the end" is the thing that gets cut when the schedule
tightens.

**The recommendation is a split**: items 1 and 7 before M5 because they are
small and one of them is a dead end for anyone who does not already know the
URL; the rest grouped after M6.

---

## 1. `/projects` is a dead end 🔴

**What happens.** From the home page, the only link into the product goes to
`/projects`. That page renders one heading and the words *"Built in M3."* — it
is a stub that was never replaced when M3 shipped. There is no link anywhere to
`/editor/scratch`, so a person who does not already know that URL cannot reach
the editor at all.

**What it actually is.** Not a design problem. A route that was stubbed and
forgotten, three milestones ago.

**Size.** Small either way. Two options: build the real project list (`GET
/projects` already exists and returns everything it needs), or redirect
`/projects` to a new project until the list is built. The first is perhaps half a
day; the second is ten minutes and removes the dead end immediately.

**Do this one first regardless of what happens to the rest of this document.**

---

## 2. Play sits in the wrong place

**What happens.** The play button is in the header bar at the very top of the
window. The picture it plays is in the middle of the screen and the playhead it
moves is at the bottom. Nothing connects the control to either.

**What it actually is.** The header was built as a generic application bar and
the transport was put in it because there was somewhere to put it. Every editor
this product is compared to puts the transport under the picture or above the
timeline.

**Size.** Small. Moving it is a layout change; the behaviour and the keyboard
shortcut are unaffected.

**Worth deciding at the same time:** the transport currently has play/pause only.
Frame-step, jump-to-start and jump-to-end already exist as keyboard shortcuts
with no visible control.

---

## 3. The toolbar is thin for a video editor

**What happens.** The toolbar offers Split, Duplicate, Delete, Add title, Undo,
Redo — and a line of text pointing at the AI panel. There is no manual colour
control, no audio control beyond two fades in the inspector, and no effects.

**What it actually is.** An honest consequence of the milestone order. M4's job
was to prove the AI pipeline, and it did; nothing scheduled the manual
equivalents. A user who wants to warm an image slightly has to run a colour
analysis job and accept what it recommends.

**Size.** Medium to large, and **it is a scope question before it is a design
one**. The parts that are cheap because the data already exists and is already
saved:

- colour grade by hand — the `effects` entry and the five LUTs are shipped, so
  this is a picker and a strength slider over machinery that already works
- volume and fades — already in the inspector, arguably in the wrong place

The parts that are genuinely new work: any effect the renderer does not already
implement. Those need to land in the export renderer too, or the preview and the
exported file will disagree — which is the one failure this project has been most
careful to avoid.

**Decided, 22 August:** this pass ships manual control only for what the engine
already implements — the colour grade picker and strength slider over the five
LUTs already shipped, and the volume/fade controls relocated out of their current
spot. **No new effect type in this pass.** Anything the renderer does not already
implement is Phase 2 scope, because it has to land in the export renderer at the
same time or the preview and the exported file disagree — the one failure this
project has been most careful to avoid, and not something to risk for a usability
pass.

---

## 4. The media panel is mostly empty, and the right panel does not scale

Two items from the original list, decided together because the same choice
answers both.

**What happens, left.** The left panel is an upload button and a list of files —
functional, and a lot of empty space on a new project.

**What happens, right.** The right side is two components stacked: the inspector
(clip properties, fixed height) and the tools panel (the three AI tools, fixed
width). Built separately, they look separate, and Phase 2 adds four more tools
(face mapping, lip sync, denoise, dereverb) to a structure that is already full.

**Decided, 22 August: the left panel becomes a mode rail, the right panel stays
the inspector and nothing else.**

Concretely — an icon rail down the left edge, the same visual language as the
transport icons at the top: **Media** (today's upload list, unchanged), **Titles**,
**Audio**, **Colour**, **Captions / Smart trim** (the current AI tools panel,
moved here as two more modes rather than a third stacked block), with room for
whatever Phase 2 adds as one more icon rather than one more stacked panel. One
mode is active at a time; its content fills the left panel where the media list
sits today.

The right panel keeps exactly the job it already does well at small size —
properties of whatever is selected — and stops being asked to also hold a
scrolling list of tools. That is what makes it scale: the rail grows by one icon
per tool, the inspector's job never changes shape.

This resolves the right-panel-does-not-scale half directly: instead of "the
inspector *and* the tools panel", there is one panel whose job is properties, and
a rail whose job is everything else. It answers the empty-left-panel half by
giving that panel a purpose beyond a file list without inventing a second,
disconnected navigation system.

---

## 5. The adjustment sliders are too large, and one is unexplained

Three separate problems that happen to sit in the same panel.

**The size.** They are native `<input type="range">` elements at full panel
width, one per row, inside a panel 176 pixels tall. Four of them fill it. The
charter set colours and states; it never set a control density, so these are the
browser's defaults.

**No typed input.** There is no way to enter a value. Setting speed to exactly
1.25 means dragging until the readout agrees. Every value in that panel is a
number with a known range — each one could be a small numeric field with the
slider as a secondary control, or with no slider at all.

**"Fade in" and "Fade out" are unlabelled.** They are the audio volume ramp at
the start and end of the clip. Nothing on screen says so, and the words alone are
ambiguous in a tool that also has video transitions called *fade*. This is a
tooltip and possibly a better name — the smallest item in this document and the
one most likely to be silently confusing a user right now.

**Size.** Small. Mostly component work in one file, plus a decision about what a
numeric control looks like across the product.

---

## 6. Timeline zoom is imprecise

**What happens.** There is a zoom slider, and it is coarse. There is *also* a
zoom on Ctrl/⌘ + wheel that anchors on the cursor, which is precise — but nothing
on screen advertises it, so it may as well not exist.

**What it actually is.** Half discoverability, half a conflict nobody has
resolved. Plain wheel currently scrolls the timeline horizontally. Making plain
wheel zoom instead means deciding what scrolls, and horizontal scrolling on a
timeline is not optional.

**Decided, 22 August: keep the current behaviour** — plain wheel scrolls,
Ctrl/⌘+wheel zooms. It is the convention every browser already teaches, so it is
the one that costs nothing to relearn. What remains is purely the discoverability
half: the shortcut needs to be visible somewhere on screen — a hint near the zoom
slider, or in the empty state before anything is on the timeline — rather than
living only in the code. The imprecision complaint was really *"the slider is
coarse and I did not know the precise way existed"*, and it goes away once the
precise way is found.

---

## 7. The timeline looks bolted on

**What happens.** The timeline is a fixed-height block at the bottom of the
window with no border and no visual relationship to anything above it. All of its
legibility comes from inside the component — the ruler, the lane separators.

**What it actually is.** The page layout was assembled as three independent
regions and never composed as one surface. The timeline is the most-used part of
the editor and the least integrated.

**Size.** Small if it is a border, a background and some spacing. Larger if it is
also the resizable divider it probably wants to be — a timeline whose height the
user cannot change is a limitation people notice quickly on a long project.

---

## Summary

| # | Item | What it is | Size | Scope |
|---|---|---|---|---|
| 1 | `/projects` dead end | Forgotten stub, blocks access | 🔴 Small | — |
| 2 | Play button placement | Layout | Small | — |
| 3 | Thin toolbar | Manual controls for what the renderer already does; new effects to Phase 2 | Medium | ✅ Decided 22 Aug |
| 4 | Empty media panel + right panel does not scale | Left becomes a mode rail; right stays the inspector alone | Medium | ✅ Decided 22 Aug |
| 5 | Oversized sliders, no typed input, unlabelled fades | Component work | Small | — |
| 6 | Imprecise zoom | Keep current behaviour; make it discoverable | Small | ✅ Decided 22 Aug |
| 7 | Timeline looks bolted on | Layout composition | Small–medium | — |

Every scope question this document raised is now answered. What is left is
craft work — sizes, spacing, the rail's exact visuals — and the project lead's
decision on when to schedule it, per *"When to do this"* above.
