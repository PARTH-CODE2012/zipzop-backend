# M4 build notes — the job pipeline, and the three tools through it

**What M4 decided, what it found, and what is left.**

| | |
|---|---|
| **Milestone** | M4 — *"you run captions on a real clip, watch progress, see the words appear in time, and fix a misspelled name"* |
| **Dates** | 20-21 August 2026 |
| **Read it after** | [`10-m4-readiness.md`](10-m4-readiness.md), which this follows the order of |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M4 |

Proven on a running stack — register, upload, ingest, estimate, create, watch a
Celery worker claim it, read the result — for all three tools.

**Captions**, on a clip of real speech:

```
  0-400    Hello        c=0.89 em=0.00
  400-960  everyone,    c=0.90 em=1.00
  1220-1500 welcome     c=0.93 em=0.26
```

**Smart trim**, on a clip with a deliberate two-second pause:

```
analysed: 7455 ms | kept: 5301 ms
  2532-4686  silence   c=0.99
```

**Colour analysis**:

```json
{ "lut": "cinematic_warm", "strength": 0.66,
  "scene": { "exposure": "normal", "whiteBalance": "neutral", "contrast": "flat" } }
```

Credits reserved and settled each time, and a replayed idempotency key returning
the same job rather than a second one.

---

## 1. What was decided

### The enqueue happens after the commit, and the architecture doc's step 6 is wrong

[`03-backend-architecture.md`](03-backend-architecture.md) §5.2 lists job
creation as seven steps *in one transaction*, the sixth being "enqueue the
Celery task". Doing that literally hands a worker a job id that no other
connection can see yet: the task starts, reads nothing, and fails a job the user
has not yet been told exists. It is a race that only shows up under load, which
is the worst kind.

`POST /jobs` therefore commits **first** and sends the task afterwards. The
credits and the job row are still one transaction — that property is the whole
point and is untouched — but the message goes out only once the row is durable.
The worker also treats "job not visible" as a transient failure and retries, so
the ordering is defended in two places rather than trusted in one.

### Concurrency is enforced at the claim, not at creation

§5.3 says the caps are "enforced at creation" and then, two sentences later,
that beyond the limit *"jobs stay `queued` and start as slots free up — the
request still succeeds"*. Both cannot be true of the same check. The second is
the one the client depends on, so the cap lives in the claiming `UPDATE`: a
worker will not take a job whose owner is already at their limit, and the task
retries in fifteen seconds. `POST /jobs` never fails for concurrency.

### Cost and estimate are one function, because the alternative is a bug report

Contract §6.1 asks for the estimate to be *"exact, not indicative"*. Two
implementations that agree today are two implementations that disagree the first
time one is edited, so `services/pricing.py` is called by both endpoints and a
test asserts the two agree field for field. A price on a button that differs
from the price on click is the kind of bug users report as theft.

### Colour analysis first, and it earned its place

The readiness doc's suggested order put it first because it has no external
dependency — a handful of sampled frames through ffmpeg's `signalstats`, no
transcription engine to choose. That turned out to matter more than expected:
every part of the pipeline that could be wrong — the reservation, the claim, the
progress checkpoints, the settle, the refund — was exercised against a real file
and a real worker before anything harder touched it.

---

## 2. Two defects found while building it

### 🔴 A validator that raises `ValueError` produced a 500, not a 422

`register_exception_handlers` serialised Pydantic's error list straight into a
`JSONResponse`. A `field_validator` or `model_validator` that raises
`ValueError` puts the **exception object itself** in the error's `ctx`, and
`JSONResponse` cannot encode it — so the handler whose entire job is to turn a
bad request into a readable 422 raised inside itself.

Nothing caught this before because every previous validator failure came from a
built-in type check, whose `ctx` is a string. The first hand-written one — "this
tool does not ship yet" — was the request that broke, and the thing it broke was
the error path.

Every validator in the codebase was one line away from the same fault. The fix
scrubs the error list to JSON-safe values in `errors.py`, once, for all of them.

### Refunds were landing in the wrong bucket, and it was two clocks

`jobs.created_at` is the database's `now()`. A subscription's
`current_period_start` is written by whatever granted it — Python's clock, at
registration. Comparing them directly to decide whether the billing period had
rolled over meant a job created moments before a renewal looked like a job that
had outlived one, and its refund went to `topup` rather than `plan`.

The user is still made whole either way, so nothing was lost — but the ledger
recorded an event that did not happen, and the nightly reconciliation would have
had nothing to say about it. There is now a five-second tolerance, and the
reason is written where the comparison is.

---

## 3. The transcription engine and the language list

**Decided 21 August by the project lead: self-hosted `faster-whisper`.** No
per-call cost that scales with usage, and the accuracy at this model size is
honestly the same order as a cheap third-party API — the trade the readiness
doc laid out, taken in the direction it recommended.

Three consequences worth knowing before touching this:

- **The call sits behind one function.** `transcription._transcribe_with_whisper`
  is the entire provider surface. Everything above it — captions, smart trim,
  the pipeline — speaks in `Word` objects and knows nothing about what produced
  them, which is what the readiness doc asked for so a later swap is a function
  body rather than an architecture change. The tests assert the *boundary*, not
  the model, so they keep their meaning across a change of engine.
- ⚠️ **The model downloads on first use** — ~150 MB into `~/.cache/huggingface`.
  A worker's first captions job therefore takes far longer than its estimate.
  **Warm the cache when deploying** rather than letting the first customer pay
  for it.
- **Emphasis is measured, not guessed.** The contract's `em` drives the caption
  animation, and the honest signal for it is loudness — so the audio is decoded
  once into a 20 ms RMS envelope and each word is scored against *that speaker
  in that clip*, on percentiles rather than min/max. A quiet recording still has
  emphasis, a loud one is not all emphasis, and one cough cannot flatten the
  scale. If the envelope fails, every word keeps zero and the captions still
  appear: emphasis is the animation, not the words.

### The language list, closed the same day

**English, French and Hindi.** The vision doc's "30+ languages" conflated two
different features — transcribing what was said and translating it into
something else — and phase 1 does only the first, so this is a decision about
*what speech we accept*, nothing more.

⚠️ **The engine was never the constraint.** Whisper handles ~99 languages; a
fourth is `app/services/languages.py` plus somebody who speaks it checking the
filler list. What is not free is the claim: a language in that list is one we
are saying works, and shipping thirty nobody has listened to is exactly how
"30+ languages" became a marketing number rather than a feature.

Two things fell out of it that were not obvious before writing them:

- **A named language is honoured, not treated as a hint.** `language: "de"` is
  refused rather than quietly detected — a user naming a language is telling us
  something we do not know, like which of two in the same recording they want,
  and falling back to `auto` would be right often enough that nobody would
  notice it was ignored.
- **Hindi needs both scripts.** Whisper transcribes Hindi in Devanagari, but a
  great deal of real Indian speech is Hinglish and comes back in Latin, so the
  filler list carries `मतलब` and `matlab` alike. It also has more words that are
  *both* filler and vocabulary than English does — `तो` is a conjunction, `वो` is
  a pronoun, `हाँ` is yes — so all of them are in the ambiguous set and are only
  cut when said in isolation.

---

## 4. The defect the browser found that no test could

The editor was opened in a browser without WebGL2. It did not degrade — it
**died**: the renderer threw while constructing, nothing caught it, React
unmounted the tree, and the page became *"Application error: a client-side
exception has occurred"*. Behind that white screen were the user's timeline,
their media and their unsaved work, with no way back to any of it.

The compositor having **no fallback is a deliberate decision**
([`04-frontend-architecture.md`](04-frontend-architecture.md) §4.4) and it
stands. The editor going down with it was never decided by anyone. Every other
part of the application works perfectly well without a picture — the timeline,
the inspector, the tools, autosave — so `PreviewBoundary` now contains the
failure and says so where the picture would be.

**No unit test could have found this**, and that is the point worth keeping: the
suite runs in jsdom, which has no WebGL at all, so the renderer is never
constructed there. It took opening the application in something that behaves
like a browser and is not one.

---

## 5. What is deliberately not done

- **Two of the three caption styles.** `caption_bold` ships. The other two are
  design work — a look, not a mechanism — and inventing them in code would
  produce two styles nobody chose.
- **The mock server and fixtures.** Moved here from M0 and still open. The
  fixture list in the checklist is worth building as written; a 2,000-word
  result with a deliberately misspelled name is the one that exercises the
  milestone's own closing condition without a worker.
- **One LUT at a time in the preview.** The renderer holds a single 3D texture,
  so a project with two differently graded clips shows the first one's look on
  both. Giving it one per clip is a change to the shader and the video pool
  rather than to the catalogue. Written down at the effect that loads it.
- **Transitions in the preview**, still — unchanged from M3, still waiting on
  the contract decision in [`09-m3-notes.md`](09-m3-notes.md) §5.

---

## 6. Applying a result, and the conversion that makes it correct

⚠️ **Every job result is in asset time. The timeline is not.** A clip trimmed to
start four seconds into its media and played at 1.5x has a clock of its own, and
a caption placed at the millisecond the server reported lands somewhere else —
early, late, and drifting further the longer the clip runs.

`editor/tools/results.ts` owns the conversion, and its tests use a clip that is
**both trimmed and sped up**, because a clip starting at zero at 1x makes the two
clocks identical and a test against that one passes whether the conversion exists
or not.

Two rules it obeys that each cost a bug if forgotten:

1. **Clip to the window.** The server analysed the whole file; the clip shows
   part of it. Words outside are dropped rather than placed, or trimming a clip
   silently gains captions for footage nobody can see. A word *straddling* the
   edge is kept and clipped — half a word of audio is still a word the viewer
   hears.
2. **Speed divides.** Four seconds of media in a 2x clip is two seconds of
   timeline.

Each tool then lands as **one commit**. A minute of speech is ~150 clips and
1,800 words is 1,800 clips; committing per clip would make undoing a captions run
1,800 presses of ⌘Z. Re-running replaces the previous run rather than doubling
it, and a hand-typed title is never touched — a tool silently deleting text
somebody wrote would be unforgivable.

Smart trim closes the gaps behind its cuts, rippling the clips after it on the
**same track only**. ⚠️ Captions already on the text track do not follow, so the
order is trim first, caption second.

---

## 7. Numbers worth knowing

| | |
|---|---|
| Cost | `captions` 2 · `smart_trim` 1 · `color_analysis` 1 · `export` 2 credits per minute, rounded up, minimum one |
| The contract's own example | 623 480 ms of captions = **22 credits**, and the implementation returns 22 |
| Inline result limit | 256 KB, measured on the serialised JSON — above it the result goes to S3 |
| Retries | 3, backing off 10 s · 30 s · 90 s, transient failures only |
| Priority bands | 0 / 10 / 20 / 30, from `plans.queue_priority`, as Celery `priority_steps` |
| Languages | `en`, `fr`, `hi` — plus `auto`, which detects |
| Transcription model | `faster-whisper` `base`, CPU, int8. ~150 MB, downloaded on first use |
| Looks | five `.cube` files at 17³, shared byte-for-byte with the export renderer |

🟠 `SECONDS_PER_MINUTE_OF_MEDIA` in `pricing.py` is a **heuristic, not a
measurement**, and carries the same caveat the architecture doc puts on its own
credit numbers: recalibrate from real jobs. Nothing in the product may present
it as an SLA.
