# UI Charter

**The palette, the typeface, the spacing, the states and the motion. Everything the interface is allowed to look like, and the reasons.**

| | |
|---|---|
| **Version** | 1.0 — derived from the approved baseline |
| **Date** | 17 August 2026 |
| **Baseline** | **A2 Studio** — [`ui-directions/ui-directions-modern/A2-studio-large-preview.html`](ui-directions/ui-directions-modern/A2-studio-large-preview.html), approved by the project lead on 17 August 2026 |
| **Audience** | Anyone writing a component or a stylesheet |
| **Applies to** | `frontend/src/**` — the product. The marketing site is not in scope |
| **Lands in** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M3, *Frontend — visual charter* |
| **Depends on** | [`04-frontend-architecture.md`](04-frontend-architecture.md) §5, §9 — the timeline's rendering model and the performance budget constrain half of what follows |

> **Nothing in `frontend/` has been changed yet.** This document is the specification; §13 is the block that replaces `@theme` in `frontend/src/styles/globals.css` when the work is picked up. M2 deliberately routed every colour the editor paints through a token so that applying this charter is one file, not a sweep.

> **Four directions were compared before A2, and five before those.** They are kept in [`ui-directions/`](ui-directions/index.html) as the record of what was rejected and why. If a decision below looks arbitrary, the alternative it beat is probably sitting in that folder.

---

## 1. How to use this document

Read §2 once. It contains the five rules that decide arguments, and every one of them exists because breaking it produces a specific, known failure.

After that, treat §3 to §12 as reference. Every value is a token; **no component file may contain a literal colour, radius, duration or font stack.** If you need a value that is not here, that is a gap in this document — add it here first, then use it. A one-off literal in a component is how a design system dies, and it dies quietly.

The charter describes the **editor**, because the editor is M3 and the editor is where the product is hard. Pricing, projects, login and the paywall inherit the same tokens; §12 says how their layouts differ.

---

## 2. The five rules

**1 · Neon yellow is never body text.** `#FFE81F` on near-black measures around 16:1, which sounds excellent and is exactly the problem: at 11–13 px it glows and the edges bloom. Yellow marks *one* of three things — the active state, a live data value, or a fill you read black text on top of. Sentences are `--color-ink`. A pricing page with a yellow paragraph is a bug report, not a style choice.

**2 · Blur and translucency stop at the chrome.** The chrome is a bounded set: the tool rail, the media panel, the contextual bar, the transport, the timeline container. Five elements, five compositor layers. A `backdrop-filter` on a timeline clip means one layer per clip, and [`04-frontend-architecture.md`](04-frontend-architecture.md) §9 requires the timeline to stay smooth at **500 clips**. Clips are opaque, flat, and cheap. This is the reason the timeline is allowed to look more technical than the rest of the application — it is not a stylistic concession, it is the budget.

**3 · No state is carried by hue alone.** Every state changes at least **two** of: fill, edge, weight, position, icon. This is not only for colour-blind users, though it is that too; it is also because a timeline at 500 clips is 3 px per clip on screen and hue is the first thing that stops being readable. §9 is the table that proves it for the four states the timeline needs.

**4 · Nothing driven by the clock or by the pointer is animated.** The playhead, a clip mid-drag, the waveform, a scrub — these follow input at 60 fps and a transition on them reads as lag. Animation belongs to *discrete* state changes: a panel opening, a hover, a value committing. Getting this wrong is how a Canva-like editor stops feeling like an editor.

**5 · Anything that costs credits looks different from anything that does not.** Split, trim and fade are free. Captions, smart trim, colour grading and export spend money. They carry the accent tint, the sparkle mark and the price. A user must never learn what a button costs by clicking it.

---

## 3. Colour

All contrast figures below are computed against `--color-surface` (`#0A0A0C`) unless stated, using the WCAG 2.1 relative-luminance formula.

### 3.1 Surfaces

The chrome is translucent white over near-black. The ladder below is the **composite** of each translucency over `--color-surface`, and it is what you use anywhere blur is forbidden — which is the whole timeline.

| Token | Solid | As chrome | Used for |
|---|---|---|---|
| `--color-surface` | `#0A0A0C` | — | The application background. Not pure black: pure black leaves the translucent panels with nothing to lift off |
| `--color-surface-2` | `#151517` | `rgba(255,255,255,0.045)` | Panels, cards, the timeline container |
| `--color-surface-3` | `#202022` | `rgba(255,255,255,0.09)` | Raised or hovered surface, inputs |
| `--color-rule` | `#26262B` | `rgba(255,255,255,0.09)` | Hairlines. **1 px, never 2** — 2 px belongs to the arcade direction we rejected |
| `--color-canvas` | `#000000` | — | The preview stage only. True black, so the picture is the brightest thing on screen |

`--color-canvas` is the one place pure black is correct, and the reason is the whole point of A2: the video is the subject, the interface is not.

### 3.2 Ink

| Token | Value | Contrast | Used for |
|---|---|---|---|
| `--color-ink` | `#F0F0F4` | 17:1 | Values, headings, selected labels. The colour of anything that matters |
| `--color-ink-2` | `#9A9AA6` | 7.1:1 | Body copy, ordinary labels, inactive icons |
| `--color-ink-3` | `#7A7A87` | 4.6:1 | Small labels, section headers, timecode rulers. **This is the floor for anything a user has to read** |
| `--color-ink-faint` | `#4E4E58` | 2.2:1 | Grid lines, ruler ticks, disabled glyphs. **Never text** |

> **This corrects the baseline.** A2 used `#61616E` for its section labels, which measures **3.2:1** — it passes for large text and for component boundaries, and fails for an 11 px label, which is exactly what it was used on. `--color-ink-3` is the lightened replacement. If a mockup and this table disagree, this table wins.

### 3.3 The accent

| Token | Value | Used for |
|---|---|---|
| `--color-accent` | `#FFE81F` | Active state, primary fill, playhead, live data values |
| `--color-accent-ink` | `#0A0A0C` | Text and icons **on top of** an accent fill — 16:1, the most legible pairing in the product |
| `--color-accent-soft` | `rgba(255,232,31,0.14)` | The background of an active tool, an AI panel, a selected tab |
| `--color-accent-line` | `rgba(255,232,31,0.5)` | 1 px ring on a selected element |
| `--color-accent-glow` | `rgba(255,232,31,0.22)` | The single permitted glow — `0 0 14px`, on a selected clip only |

One accent, one hex. There is no secondary brand colour and no gradient. A2 earns its warmth from a single ambient wash — `radial-gradient(440px 240px at 20% 6%, rgba(255,232,31,0.055), transparent 70%)` on the application background — and that wash is the only gradient in the product.

### 3.4 Semantic colours

| Token | Value | Used for |
|---|---|---|
| `--color-danger` | `#FF5C5C` | Failed job, destructive confirm, validation error |
| `--color-warning` | `#FF9A2E` | Approaching a limit, low credits, stale version |
| `--color-success` | `#3DDC97` | Job complete, saved, payment accepted |

> **Warning is orange, not yellow, and this is not negotiable.** Yellow already means *active*. A yellow "you are running out of credits" banner is indistinguishable from a yellow "this tool is selected", and the one place that collision lands is the paywall — the screen where being misread costs a subscription. The gap between `#FFE81F` and `#FF9A2E` is deliberately wide enough to survive a bad monitor.

### 3.5 The timeline

Opaque, flat, no blur, no gradient, minimal radius. Everything here is read either by CSS or — for the waveform and the grid — by canvas code, which is why they are hex rather than translucency.

| Token | Value | Note |
|---|---|---|
| `--color-track` | `#121214` | Lane background |
| `--color-track-header` | `#151517` | The lane's label column |
| `--color-clip` | `#252527` | A clip at rest |
| `--color-clip-hover` | `#343435` | Pointer over it |
| `--color-clip-border` | `#33333A` | Only drawn when a clip is under 6 px wide, where fill alone stops separating neighbours |
| `--color-clip-selected` | `#FFE81F` | With `--color-accent-ink` text |
| `--color-clip-selected-border` | `rgba(255,232,31,0.5)` | 1 px ring |
| `--color-clip-dragging` | `#312E0F` | The opaque equivalent of the accent at 16% — computed, not eyeballed, so it matches the chrome's tint without costing a layer |
| `--color-track-muted-opacity` | `0.32` | Applied to the whole lane |
| `--color-ruler` | `#0F0F11` | |
| `--color-ruler-tick` | `#2A2A30` | |
| `--color-ruler-label` | `#7A7A87` | `--color-ink-3`, because timecodes are read |
| `--color-playhead` | `#FFE81F` | 2 px, `--radius-xs`, with a 10 × 10 handle |
| `--color-grid-major` | `rgba(255,255,255,0.09)` | One line per ruler label |
| `--color-grid-minor` | `rgba(255,255,255,0.035)` | Five subdivisions between majors |
| `--color-snap-guide` | `#FFE81F` at 55% | Appears only while dragging, disappears on drop |
| `--color-waveform` | `#8A8A96` | Drawn into the canvas |
| `--color-waveform-selected` | `#3D3806` | **Required.** A `#8A8A96` waveform on a yellow selected clip is invisible; the canvas has to switch tokens when the clip is selected, and nothing else in CSS will do it for you |

The grid is the element the project lead specifically asked to keep technical. It is a two-level repeating background — majors aligned to the ruler labels, minors at a fifth of that — with 1 px hairlines and no rounding. It is the one part of the interface that is allowed to look like a tool rather than a product.

---

## 4. Typography

| Role | Family | Weights | Fallback |
|---|---|---|---|
| Interface | **Plus Jakarta Sans** | 400, 500, 600, 700 | `system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` |
| Numerals | **IBM Plex Mono** | 400, 500 | `ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace` |

Plus Jakarta Sans is geometric enough to read as modern and has a wide enough aperture to survive 11 px on a dark background. 700 is reserved for the wordmark; **800 and 900 do not exist in this product.**

**Every number that changes while the user watches is mono, with `font-variant-numeric: tabular-nums`.** Timecodes, durations, credit balances, file sizes, percentages, job progress. A proportional `1` is narrower than a `0`, so a playing timecode jitters, and a jittering timecode looks broken in a way users report but cannot describe.

### Scale

| Step | Size | Weight | Tracking | Used for |
|---|---|---|---|---|
| `text-2xl` | 24 px | 600 | −0.02em | Page titles — pricing, projects |
| `text-xl` | 20 px | 600 | −0.02em | Section headings, modal titles |
| `text-lg` | 16 px | 500 | −0.01em | Card titles, the wordmark at 700 |
| `text-base` | 13 px | 400 | 0 | Interface default — buttons, list rows, prose |
| `text-sm` | 12 px | 400 | 0 | Dense areas: inspector rows, clip labels, timecodes |
| `text-label` | 11 px | 600 | 0.05em, uppercase | Section labels only. Never a sentence |

**11 px is the floor.** The baseline mockups went to 10 px to fit a 680 px comparison frame; the real editor has a full window and does not need it.

Line-height is 1.4 for interface and 1.6 for prose. Nothing is justified, nothing is letter-spaced except `text-label`.

---

## 5. Space, radius, density

Spacing is a 4 px grid: **4, 8, 12, 16, 24, 32**. Inside dense components — a timeline lane, an inspector row — 6 and 10 are permitted. Nothing else is.

| Token | Value | Used for |
|---|---|---|
| `--radius-pill` | `999px` | Buttons, chips, transport, the contextual bar |
| `--radius-lg` | `16px` | The application frame, modals |
| `--radius-md` | `13px` | Panels, cards, the timeline container |
| `--radius-sm` | `10px` | Tool tiles, thumbnails, inputs |
| `--radius-xs` | `5px` | Timeline clips, the playhead |

**Radius shrinks as density rises.** A 13 px radius on a 19 px clip eats a quarter of its height and the clip stops reading as a rectangle you can trim to a frame. The timeline is the densest surface in the product, so it gets the smallest radius — and this, again, is why the grid looks the way it does.

One rule inherited from the rejected directions: **no rounded corner on a single-sided border.** If a component uses `border-left` as an accent, its radius is 0.

---

## 6. Surfaces, translucency, elevation

A chrome panel is exactly three declarations:

```
background: rgba(255,255,255,0.045);
border: 1px solid rgba(255,255,255,0.09);
backdrop-filter: blur(14px);
```

**The blur budget is five elements per screen** — rail, media panel, contextual bar, transport, timeline container. That number is a hard ceiling, not a guideline, and it is why the inspector in A2 is a panel rather than a per-clip popover. If a sixth element needs to look translucent, it uses `--color-surface-2` solid and nobody notices.

Shadows: there are two, and no others.

| | Value | Used for |
|---|---|---|
| Focus ring | `0 0 0 2px rgba(255,232,31,0.6)`, offset 2 px | `:focus-visible`, everywhere, never removed |
| Drag lift | `0 5px 16px rgba(0,0,0,0.5)` | An element the pointer is currently carrying |

No ambient shadows on cards. Depth comes from the translucency and the hairline, which is cheaper and does not muddy a dark interface.

---

## 7. Motion

| Token | Duration | Curve | Used for |
|---|---|---|---|
| `--ease-standard` | 200 ms | `cubic-bezier(.22,1,.36,1)` | Panel open, element enter, hover lift, layout change |
| `--ease-micro` | 160 ms | `ease-out` | Hover colour, icon tint, background change |
| `--ease-spring` | 240 ms | `cubic-bezier(.34,1.56,.64,1)` | Press feedback on the primary CTA **only** |

**Never animated**, per rule 4: playhead position, clip position during a drag, scrub, waveform, zoom while the pointer is down, and any value bound to the playback clock.

**Always animated**: a panel opening or closing, a tool becoming active, a hover, a job's progress bar, a value committing, an element entering for the first time.

The two ambient loops A2 uses are deliberate and both are slow enough to sit below notice: the playhead breathes between 75% and 100% opacity over 2.4 s, and a running job's progress bar oscillates a few percent over 2.6 s so that "working" is distinguishable from "stuck at 62%".

Under `prefers-reduced-motion: reduce`, every duration above collapses to 0 ms except opacity fades, which cap at 100 ms. Transforms are dropped entirely — including the drag lift, which falls back to the ring alone.

---

## 8. Component states

Six states, and every component answers all six or documents why one does not apply.

| State | Signal | Notes |
|---|---|---|
| **Rest** | `--color-ink-2` on transparent | |
| **Hover** | `--color-surface-3` background, ink to `--color-ink`, `translateY(-1px)` | `--ease-micro`. Hover never changes size — only lift and colour |
| **Focus-visible** | Focus ring from §6 | Keyboard only. `:focus` without `-visible` produces a ring on every mouse click and users read it as a stuck element |
| **Active / selected** | `--color-accent-soft` background, `--color-accent` ink | Or a full accent fill with `--color-accent-ink` for primary actions |
| **Disabled** | `opacity: 0.4`, `pointer-events: none`, plus a reason | **Never colour alone.** A disabled export button says *why* — out of credits, 4K not on this plan, nothing on the timeline |
| **Error** | `--color-danger` ink, 1 px `--color-danger` border, and a message | The message is required. A red border with no text is a shrug |

Primary buttons are a filled accent pill with `--color-accent-ink` text. Secondary buttons are a `--color-surface-2` pill with `--color-ink-2` text. Destructive buttons are `--color-danger` **outlined**, never filled — a filled red button next to a filled yellow one turns a confirmation dialog into a traffic light.

---

## 9. The timeline's four states — the contract

`frontend/src/styles/globals.css` already carries a comment naming the three clip states plus the muted lane as the questions a charter has to answer. These are the answers, and each satisfies rule 3 by changing at least two properties.

| State | Fill | Edge | Second signal | Third |
|---|---|---|---|---|
| **At rest** | `--color-clip` `#252527` | none | — | — |
| **Hover** | `--color-clip-hover` `#343435` | none | cursor changes at the trim handles | — |
| **Selected** | `--color-clip-selected` `#FFE81F` | 1 px `--color-clip-selected-border` | label flips to `--color-accent-ink` at weight 600 | `--color-accent-glow` at `0 0 14px` |
| **Dragging** | `--color-clip-dragging` `#312E0F` | 1 px solid `--color-accent` | lifted `translateY(-2px)` with the drag shadow | the vacated slot shows a 1 px dashed outline |
| **Muted lane** | whole lane at `--color-track-muted-opacity` | — | lane icon swaps to `ti-volume-off` | header shows `M` |

Two details that fall out of this and are easy to miss:

- **The waveform must switch colour with the selection** (§3.5). The canvas reads the token; CSS cannot inherit into it.
- **Selected is the highest-contrast element on the entire screen** at roughly 16:1, brighter than the preview's own picture. That is intentional. Finding your selected clip in a dense timeline is a visual search task, and visual search is won by contrast, not by taste. It is also the one place rule 1 does not apply, because the yellow is a fill and the text on it is black.

---

## 10. Icons

**Tabler outline**, and only outline — filled variants are a different visual language and mixing them reads as an accident. Nominal stroke 1.5 px, sizes 15 px inline, 17 px in a tool tile, 20 px maximum. Decorative icons take `aria-hidden="true"`; an icon-only button takes an `aria-label`.

| Feature | Icon | Feature | Icon |
|---|---|---|---|
| Select | `ti-pointer` | Media | `ti-photo` |
| Split | `ti-scissors` | Text | `ti-typography` |
| Trim | `ti-arrows-horizontal` | Audio | `ti-music` |
| Volume | `ti-volume` | Muted | `ti-volume-off` |
| Crop / reframe | `ti-crop` | Video lane | `ti-movie` |
| Transition | `ti-transition-right` | Credits | `ti-coins` |
| Undo / redo | `ti-arrow-back-up` / `ti-arrow-forward-up` | Snap | `ti-magnet` |
| **AI, all tools** | `ti-sparkles` | Captions | `ti-message-2` |
| Smart trim | `ti-wand` | Colour grade | `ti-palette` |

`ti-sparkles` is the mark that means *this costs credits*. It appears on the AI rail entry, on the Magic button, and on nothing else.

---

## 11. AI tools look different because they cost money

Rule 5, made concrete. An AI action is:

- `--color-accent-soft` background with `--color-accent` ink, never the grey of an ordinary tool
- prefixed by `ti-sparkles`
- **labelled with its price** before it is pressed — the estimate arrives when the panel opens, per [`04-frontend-architecture.md`](04-frontend-architecture.md) §7, and it sits on the button, not in a tooltip
- while running: a progress bar in `--color-accent` with the percentage and the credits held, in mono
- on failure: `--color-danger`, the reason, a retry, and the sentence that says the credits came back

In A2 the three tools live behind one **Magic** entry rather than three toolbar buttons. That is a deliberate departure from the original brief's "one dedicated button per feature", and the trade was accepted with the direction: it keeps the contextual bar short, and the panel it opens does list all three as named, labelled, priced buttons. The brief is satisfied one level in.

---

## 12. Layout

The editor, at the approved proportions:

| Region | Size | Behaviour |
|---|---|---|
| Top bar | 40 px | Wordmark, project chip, credits, avatar, Export. Never scrolls |
| Tool rail | 46 px | Fixed. Icon over an 11 px label, `--radius-sm` tiles |
| Media panel | 110 px, collapsible | Collapses to 0 with the chevron, giving the canvas 118 px more |
| Canvas | Everything left | Minimum 320 px wide. `--color-canvas`, `--radius-md` |
| Timeline | 150 px, resizable 110–320 px | Docked. Three lanes at 24 px, ruler at 16 px |

The contextual bar and the transport **float over the canvas** rather than taking bands above and below it. That single change is what earned A2 its approval — it gives the preview roughly 2.3× the area the un-rebalanced version had.

> ⚠️ **The floating bars must not cover the picture.** On 9:16 the top and bottom of the frame are where creators put their hook and their captions, and they are also where the bars sit. Three rules resolve it: the frame is sized to fit *between* the bars rather than behind them; both bars fade out on `--ease-micro` during playback and during a drag; and the preview offers a safe-area guide — the outer 12% top and bottom — because [`01-product-vision.md`](01-product-vision.md) §7 already flags that captions placed where TikTok puts its own interface get covered up. A "full frame" mode may let the bars overlap, but only when the user asks for it.

Below 1024 px the media panel collapses by default. Below 768 px the editor is not supported in phase 1 — mobile is phase 3, and a timeline is not a small-screen interaction.

The other screens inherit every token and change only their container: **pricing** and **projects** are centred at a 1100 px maximum with 24 px rhythm and no rail; **login** is a single `--radius-md` card on `--color-surface`; the **paywall** is a modal with the same card treatment and a `--color-warning` header when it fires on a limit rather than on a choice.

---

## 13. The token block

This replaces the `@theme` block in [`../frontend/src/styles/globals.css`](../frontend/src/styles/globals.css). It is listed here so the charter is self-contained and reviewable before anyone touches the frontend; **the file itself is unchanged as of this version.**

```css
@theme {
  /* Surfaces */
  --color-surface: #0a0a0c;
  --color-surface-2: #151517;
  --color-surface-3: #202022;
  --color-rule: #26262b;
  --color-canvas: #000000;

  /* Ink */
  --color-ink: #f0f0f4;
  --color-ink-2: #9a9aa6;
  --color-ink-3: #7a7a87;
  --color-ink-faint: #4e4e58;

  /* Accent */
  --color-accent: #ffe81f;
  --color-accent-ink: #0a0a0c;
  --color-accent-soft: rgba(255, 232, 31, 0.14);
  --color-accent-line: rgba(255, 232, 31, 0.5);
  --color-accent-glow: rgba(255, 232, 31, 0.22);

  /* Semantic */
  --color-danger: #ff5c5c;
  --color-warning: #ff9a2e;
  --color-success: #3ddc97;

  /* Timeline */
  --color-track: #121214;
  --color-track-header: #151517;
  --color-clip: #252527;
  --color-clip-hover: #343435;
  --color-clip-border: #33333a;
  --color-clip-selected: #ffe81f;
  --color-clip-selected-border: rgba(255, 232, 31, 0.5);
  --color-clip-dragging: #312e0f;
  --color-track-muted-opacity: 0.32;
  --color-ruler: #0f0f11;
  --color-ruler-tick: #2a2a30;
  --color-ruler-label: #7a7a87;
  --color-playhead: #ffe81f;
  --color-grid-major: rgba(255, 255, 255, 0.09);
  --color-grid-minor: rgba(255, 255, 255, 0.035);
  --color-waveform: #8a8a96;
  --color-waveform-selected: #3d3806;

  /* Chrome — translucency, and the blur budget of §6 */
  --chrome-fill: rgba(255, 255, 255, 0.045);
  --chrome-edge: rgba(255, 255, 255, 0.09);
  --chrome-blur: 14px;

  /* Radius */
  --radius-pill: 999px;
  --radius-lg: 16px;
  --radius-md: 13px;
  --radius-sm: 10px;
  --radius-xs: 5px;

  /* Motion */
  --ease-standard: cubic-bezier(0.22, 1, 0.36, 1);
  --duration-standard: 200ms;
  --duration-micro: 160ms;
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
  --duration-spring: 240ms;

  /* Type */
  --font-sans: 'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'IBM Plex Mono', ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace;
}
```

Three things this block does **not** contain, on purpose:

- **The fonts are not loaded here.** Two families at four and two weights is a real payload; they are subset and self-hosted from `frontend/public/fonts/` with `font-display: swap`, not pulled from a CDN — the mockups use Google Fonts because they are single files opened by hand.
- **No component styles.** Applying this charter must not require editing a component, and if it does, that component has a literal in it and the literal is the bug.
- **No light-mode values.** See §15.

---

## 14. The accessibility floor

Not aspirations — the minimum for a merge.

- Text meets **4.5:1**; `--color-ink-3` is the lightest text colour that does, and `--color-ink-faint` is never text
- Interactive boundaries and icons meet **3:1**
- Every state satisfies rule 3 — two signals minimum, never hue alone
- `:focus-visible` is present on everything reachable by keyboard, and the ring is never overridden to `none`
- Pointer targets are **32 × 32 px** minimum in the chrome. The timeline is exempt: a clip is as wide as its duration, which is why trim handles have an 8 px hit area that extends beyond the clip's paint
- `prefers-reduced-motion` is honoured per §7
- The preview canvas carries a text alternative describing the project, not the frame
- No information is conveyed by colour, position or animation alone

The timeline is the hardest surface in the product to make accessible and the one most likely to fail a review. [`07-security.md`](07-security.md) is not where that gets caught; catch it in M3.

---

## 15. What this does not cover

| | Status |
|---|---|
| **Light theme** | ⚪ Not in phase 1. The product is a video editor and video editors are dark, but the token structure means a light theme is a second `@theme` block rather than a rewrite. Do not add light values speculatively |
| **High-contrast mode** | 🟠 `forced-colors` is unhandled. Worth an hour before launch; not worth blocking M3 |
| **The three caption styles** | 🟠 M4. Their type, outline, position and safe-area behaviour need adding here when they are designed — and the open question in [`01-product-vision.md`](01-product-vision.md) §7 about caption position has to be answered first |
| **The five LUT names** | 🟠 M4. Descriptive names, and per [`01-product-vision.md`](01-product-vision.md) §7 not named after living people |
| **Marketing site** | ⚪ Out of scope. It may take the palette; it is not bound by §12 |
| **Illustration and empty states** | 🟠 Needed for the projects page and an empty media bin. No direction chosen yet |
| **The wordmark as a drawn asset** | 🟠 Set in Plus Jakarta Sans 700 today, with `zip` in `--color-ink` and `zop` in `--color-accent`. A real logotype is a separate commission |

---

## 16. Change log

| Version | Date | What |
|---|---|---|
| **1.0** | 17 August 2026 | Written from A2 Studio, approved the same day. Corrects the baseline's label colour to meet 4.5:1 (§3.2), adds the semantic set with warning deliberately not yellow (§3.4), adds `--color-waveform-selected` because the canvas cannot inherit it (§3.5), and resolves the floating-bar overlap on 9:16 (§12) |
