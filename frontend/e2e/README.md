# End-to-end proof

M2's closing condition, checked in a real browser:

> *Ends when: you can register, upload a real video, and see it as a clip with a
> waveform, and scrub it smoothly.* — [`PHASE1-TASKS.md`](../../PHASE1-TASKS.md)

```bash
make e2e            # starts everything, runs it, tears down
make e2e-headful    # the same with a visible window
```

29 checks, from an empty database to a clip playing back.

---

## Why it drives a real browser

The M1 spike established the method: pilot Chromium over the DevTools protocol
and check the **result**, not the page's account of itself. That is what caught
both of M1's bugs, neither of which was visible on screen.

It earned its cost again here. Three defects survived a green unit suite,
a strict type-check and a lint pass, and every one of them would have shipped:

| Found | What it was | Why nothing else caught it |
|---|---|---|
| Infinite render loop | `selectClips` returned a fresh `[]` when there was no video track, so Zustand saw a new reference every read | The selector is correct in isolation. It only misbehaves once React subscribes to it — and the editor's *first paint* has no track |
| **The compositor could not draw a real proxy** | `texImage2D` threw `SecurityError: the video element contains cross-origin data`. The proxy comes from storage on another origin and `crossOrigin` was never set on the `<video>` | The element loads, plays and reports `readyState 4`. Only uploading a frame as a texture fails, and only against a real cross-origin URL — the spike's media was same-origin |
| **The ingest worker died on its second job** | The Celery task called `asyncio.run()` per job while sharing the module-level pooled engine, so job two got a connection bound to job one's dead loop | A test that ingests one file never reaches the second. The failure is `RuntimeError: got Future attached to a different loop`, which reads like an application bug |

The second one is the reason this file exists. Preview against real ingest
output is the whole point of M2's frontend, and it was completely broken while
every other signal was green.

---

## What the 29 checks cover

**Account** — registration, the free plan's 300-credit allowance granted at
signup, and that the refresh token is *not* reachable from `document.cookie`.

**Upload** — progress reported from the browser's own events, a single
presigned `PUT` straight to storage, and nothing resembling a file body going
through the API.

**Ingest** — the worker takes the upload to `ready`, probes 6000 ms as an
integer, produces a thumbnail, and every URL served carries a signature.

**Timeline** — the clip lands with the probed duration, is laid out from
milliseconds and zoom (6 s at 40 px/s is 240 px), and the waveform is read back
**pixel by pixel** from the canvas: 1679 lit pixels spanning 239 of 240
columns, from three DOM nodes rather than six hundred.

**Playback** — the real 480p proxy decodes in the browser at 854×480 from a
signed URL, the playhead scrubs, and the clock is sampled *throughout* playback
to confirm the media drives it rather than the wall clock.

**Session** — a reload restores the account from the httpOnly cookie and the
media bin refills from the server, while the timeline deliberately does not
survive. That last one is a check that M2 stops where it says it stops;
persistence is M3.

---

## Notes

**Software rendering.** `--use-angle=swiftshader`, because headless Chromium
with hardware GL crashes the GPU process on this machine — an environment quirk
recorded in [`../src/editor/playback/README.md`](../src/editor/playback/README.md),
not a finding about the compositor. **Do not read a frame rate off this run**:
the numbers M1 measured on the real GPU are the ones that mean anything.

**The fixture is generated, not committed.** `make e2e-media` builds a
six-second 720p clip with a 440 Hz tone. Six seconds is long enough for the
waveform to have shape and short enough that ingest finishes in about a second.

**Not in CI.** It needs Postgres, Redis, MinIO, ffmpeg, a Celery worker, the
API and the Next dev server all up at once. Adding that to GitHub Actions is
worth doing before M5, when export makes the browser-versus-FFmpeg comparison
a release gate — but a slow, six-service job that fails for its own reasons
would cost more attention than it saves today.
