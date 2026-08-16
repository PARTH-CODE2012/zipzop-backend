# M1 — Compositor spike

**The question:** can a browser play a timeline back — two clips, a cut, a colour grade, a text
overlay — at 60 fps at 1080p? It is the only part of phase 1 with no library to fall back on, so it
is built first ([`PHASE1-TASKS.md`](../../../../PHASE1-TASKS.md) · M1).

**The answer: yes, with room to spare.** Details below.

---

## Running it

```bash
make spike-media      # ffmpeg generates two 480p proxies and a 17³ LUT (gitignored)
make dev-frontend
```

Then <http://localhost:3000/spike/compositor>.

Space plays and pauses, arrows nudge the playhead (shift for one second). Everything else is on the
page: cut vs crossfade, grade strength, preview resolution, a switch that forces the loop onto
`requestAnimationFrame`, and a button that drops the WebGL context on purpose.

## What is here

| | |
|---|---|
| [`timeline.ts`](timeline.ts) | The document, and the asset-time ↔ timeline-time arithmetic. Pure, unit-tested |
| [`cube.ts`](cube.ts) | `.cube` parser. Pure, unit-tested |
| [`renderer.ts`](renderer.ts) | WebGL2: two video textures, the LUT as `TEXTURE_3D`, one draw |
| [`video-pool.ts`](video-pool.ts) | One `<video>` per clip: priming, throttled seeks, bounded waits |
| [`text-overlay.ts`](text-overlay.ts) | Captions on a 2D canvas above, redrawn only when the words change |
| [`engine.ts`](engine.ts) | Clock, loop, element scheduling |

Throwaway as a *page*; the modules above are written to be lifted into `editor/playback/` in M2.

---

## What it proves

Measured by driving a real Chromium over the DevTools protocol, not by reading what the page says
about itself. Where a number is quoted, it came out of that run.

### No black frame at a cut

The one thing M1 exists to rule out. Verified by reading the framebuffer back **after every single
draw** — hooked onto `drawArrays`, so no frame can slip between samples — across full playbacks in
both modes, including the loop wrap.

```
cut        min peak luminance 211/255   frames ~black 0
crossfade  min peak luminance 206/255   frames ~black 0
```

A black frame reads 0. Getting there took two fixes, both of which are the interesting part of this
milestone:

**1. Never repaint a clip that has no picture yet.** When the playhead crosses into a clip whose
element has not presented a frame, the correct output is *the frame already on screen*, not black —
so the renderer leaves the canvas untouched and reports `skipped`. Black is only right for a real
gap, where there is nothing to show by definition.

That fix has a trap inside it. Texture slots are assigned by **role** — base and over — so the base
slot changes element at every cut. "This slot already has a frame" is not "this clip has a frame",
and without tracking *which* element a slot holds, the outgoing clip's last picture gets drawn under
the incoming clip's name. Slots now carry their source, and a change of source forces a fresh
upload.

**2. Hold the playhead while media under it is catching up.** Sliding forward on the wall clock
while an element is still seeking to its in-point makes the clip ignore its own `sourceInMs`.
Measured before the fix: clip A began at asset time **0.02 s** instead of **0.50 s**, playing half a
second of footage the timeline never asked for. The playhead now holds — a few frames of latency,
always correct — and `play()` is never called on an element sitting on the wrong frame.

### The in-point arithmetic is exact

Clip A enters at 500 ms into its file, clip B at 300 ms, and they overlap across a 900 ms crossfade:

| playhead | clip A asset time | clip B asset time | mix |
|---|---|---|---|
| 5100 ms | 5600 ms ✓ | 302 ms ✓ | A → B 0 % |
| 5550 ms | 6050 ms ✓ | 750 ms ✓ | A → B 50 % |
| 5950 ms | 6450 ms ✓ | 1150 ms ✓ | A → B 94 % |

On the loop wrap, clip A restarts at 0.514 s — its in-point, not the top of the file.

### The LUT reaches the GPU intact

The sample coordinate, the RGBA8 row layout and the red-fastest ordering were checked by looking the
table up at its own grid points, where trilinear filtering must return the stored value exactly:

```
grey ramp   worst delta 0/255
red ramp    worst delta 0/255
blue ramp   worst delta 0/255
```

Zero, not "close". A wrong half-texel offset crushes the extremes, a wrong row alignment shears the
table along one axis, and a wrong axis order swaps channels — none of which are obvious on screen,
and all of which this catches.

### Crossfade

At the midpoint both clips are on screen at once and the two burnt-in timecodes are legible through
each other. The mean colour walks steadily from A to B — `(126,126,120) → (112,121,113) →
(100,118,108)` — with **no discontinuity**, where the same measurement in cut mode shows a clean
jump. Audio uses an equal-power ramp, so the two gains square to one at every point rather than
dipping at the middle.

### Losing the WebGL context

The browser can take the context away at any time. Dropped on purpose:

```
+549 ms  context lost
+793 ms  the page says so
+1523 ms context restored, program / textures / LUT rebuilt
+1644 ms the message clears
```

The picture afterwards is identical to the picture before, to the byte of its mean colour. Draws
during the outage are skipped rather than attempted, and nothing throws.

### Performance

Chromium 150, AMD Radeon (Cezanne, `radeonsi`), 1080p canvas sampling 480p proxies.

| | loop | frame cost | frames dropped |
|---|---|---|---|
| 720p · rVFC | 30.1 fps | 8.59 ms | 1 / 253 |
| **1080p · rVFC** | **30.2 fps** | **8.42 ms** | **1 / 454** |
| 4K · rVFC | 29.5 fps | 9.38 ms | 21 / 652 |
| 1080p · forced rAF | 85.4 fps | 10.76 ms | 24 / 881 |
| 4K · forced rAF | 61.7 fps | 14.32 ms | 24 / 1103 |

Two readings.

**The rVFC rows sit at 30 fps because the test clips are 30 fps** — one callback per decoded frame
is exactly right, and one dropped frame in 454 says the pipeline keeps up completely. **Forced onto
rAF, the compositor reaches 85 fps at 1080p**, which is the number that answers the milestone
question: 60 fps holds with about 40 % headroom, on integrated graphics.

**The cost is not in the shader.** 720p, 1080p and 4K all cost within a millisecond of each other,
so the per-frame time is dominated by getting a decoded video frame into a texture, not by
rasterising it. That is where to look first if the budget ever gets tight — and it means the LUT,
the crossfade and the letterbox maths are all effectively free.

---

## What it does not prove

Listed so nobody reads the section above as more than it is.

- **Safari.** Not testable from Linux, and it is the single biggest remaining risk in this
  milestone: different autoplay rules, a shorter `requestVideoFrameCallback` history, and — on iOS —
  a limit on simultaneous video playback that a crossfade needs two of. The code is written for it
  (H.264 only, `playsinline`, muted priming, an rAF fallback, no extensions beyond
  `WEBGL_lose_context`), but written-for is not tested-on. **Open the page on a Mac and an iPhone
  before M2 starts.**
- **The wall-clock fallback.** Implemented for gaps and text-only stretches, but this timeline is
  two contiguous clips, so it never runs. It gets exercised when the timeline can have gaps (M3).
- **The decoder budget.** Two clips, two elements. The three-element recycling pool exists to stop a
  fifty-clip project from exhausting the browser's decoders; that only gets tested with fifty clips.
- **Real proxies.** These are ffmpeg test patterns at a convenient bitrate, not phone footage
  through the ingest worker.
- **Audio.** `video.volume` per frame stands in for the Web Audio graph. Sample-accurate fades are
  M3.

## Notes for whoever picks this up

- **Headless Chromium with hardware GL crashes the GPU process on this machine** — the context is
  lost immediately and one of the two clips fails to decode. Software rendering
  (`--use-angle=swiftshader`) is stable and was used for every correctness check; the performance
  numbers above come from a headful run against the real GPU. This is an environment quirk, not a
  finding about the compositor.
- **The LUT here is 17³, where [`docs/04-frontend-architecture.md`](../../../../docs/04-frontend-architecture.md) §4.4 describes 33³.** Not an oversight: nothing in the lookup depends on the size, which is read from the file, and 33³ is 970 kB of text against 133 kB — enough to put a minute of black canvas in front of anyone opening this over a slow link. The production catalogue can ship either.
- The two test clips and the LUT are **generated, not committed** — `make spike-media` rebuilds them
  byte for byte. The page says so if they are missing.
- The same `.cube` file loads in FFmpeg's `lut3d`. Their outputs move the same way on every channel,
  but the browser and FFmpeg **already disagree on the ungraded frame** (`128,124,122` against
  `130,122,121`), because they do not decode H.264 to RGB identically. The M5 frame-comparison test
  has to control for that, or it will blame the LUT for a colour-conversion difference.
