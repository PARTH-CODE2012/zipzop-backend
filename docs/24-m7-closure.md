# M7 closed — the open findings, a CI that had stopped testing, and Part B on a staging stack

**5 October 2026.** The second half of M7. [`23-m7-notes.md`](23-m7-notes.md)
ended with six open findings and Part B of [`07-security.md`](07-security.md)
untested for want of a deployment. This note closes the six, runs Part B on a
production-shaped stack built on one machine, and records what that found.

| | |
|---|---|
| Backend tests | **476 → 524** — every new one that pins a fix confirmed by reverting the fix and watching it fail |
| Frontend tests | **352 → 368** |
| Findings | **All 23 from 28–29 September closed**, and **6 new**, found by running things: a CI that had not passed since 30 August, a decode bomb, account farming, scratch left by killed workers, missing response headers, and dependency advisories published since. **None open in code**; one dev-tool advisory with no fix anywhere is excepted, for the lead to sign (§6) |
| Part B | **30 checks** on the local staging stack (`make staging-check`), all passing — plus the ZAP baseline and sqlmap |
| Launch gate (§8) | 🟢 no open Critical or High · 🟢 every fix has a test · 🟢 scanners on every pull request — **all eight jobs green on GitHub since `5301ec5`, 6 October** (§10.4–10.6), the first green run since 30 August |
| Still outside the code | The cloud itself (§4). The lead's six decisions came on 6 October (§10); one action is still theirs: importing the branch ruleset (§10.3) |

---

## 1. Summary for the project lead

*One page, as `07-security.md` §9 deliverable 5 asks.*

**What was tested.** Every route, the money paths under real concurrency, the
upload and media pipeline with hostile files, the browser surface, and — new
today — the deployment: the production image behind a TLS proxy, on networks
shaped like production, with production settings and generated secrets.

**What was found, in all of M7.** 29 findings. Two Critical (concurrent jobs
spending the same credits; a Next.js version with remote-code-execution
advisories), three High, the rest Medium and Low. **All fixed**, each with a
test that fails without its fix.

**Three things worth knowing from today:**

1. **The automated tests had not run green on GitHub since 30 August.** First a
   missing font, then a lint error, then a database image that was deleted from
   Docker Hub. Three pull requests were merged into `main` while the checks were
   red. Fixed — and it is the argument for branch protection (§7).
2. **One 12 MB video could occupy a worker's whole machine** — every CPU core
   and 4 GB of memory — because it declared an 8K picture. Uploads above 4K are
   now refused before anything decodes them.
3. **One internet address could open 1,200 free accounts an hour**, each with
   300 credits and 5 GB of storage. Now ten an hour. Email verification would
   be stronger; it needs a mail provider and is your call.

**What is accepted.** One thing, waiting for your signature: a denial-of-service
advisory in `braces`, a library our *linter* uses on patterns we write
ourselves. No fixed version exists anywhere; nothing of it ships (§6).

**What only you can do** — §7: a security contact, branch protection on
`main`, confirming the patch policy and the referral policy, and deciding on
email verification. **And a host**: the last checks (§4) need the real cloud.

---

## 2. The six findings left open on 29 September

### 2.1 M7-19 🟡 The access token rode in the WebSocket URL — **fixed in code**

The register gave this to deployment: strip the query string from the load
balancer's logs. It is fixed in the protocol instead, because a log format is
one setting away from being wrong. The socket now opens with a **one-time
ticket** (`POST /v1/ws/ticket`): 30 seconds to live, spent by the handshake
(`GETDEL`), good for nothing but this socket, stored as a SHA-256. A log line
that records it records a dead string. The staging proxy redacts it anyway, and
`check.sh` proves the access log holds `ticket=REDACTED`.

### 2.2 M7-18 🟢 An open socket outlived its token — **fixed, both halves**

The ticket carries the `exp` of the access token that asked for it, and the
server closes the socket at that instant with `4001`. The client half the
register warned about is there too: `openJobStream` answers `4001` with a fresh
ticket at once — through the normal refresh, which a signed-out or suspended
account cannot pass — and any other close with backoff. A socket that flaps
(`4001` the moment it opens) backs off instead of spinning. Until now a dropped
socket was never reopened at all; it is now, which the "dropped socket"
line of the pre-launch checklist also wanted.

### 2.3 M7-20 🟢 `script-src 'unsafe-inline'` — **fixed**

A per-response nonce from `src/middleware.ts`, with `'strict-dynamic'`; Next
stamps it on every script it renders. Pages now render per request rather than
at build time — the cost of a nonce. Verified in a browser, production build
and dev server: every page hydrates, no violation; and an injected
`<img onerror>` that the old policy would have run is refused
(`script-src-attr`). `style-src` keeps `'unsafe-inline'`, documented.

### 2.4 M7-21 🟢 Abandoned multipart uploads were never aborted — **fixed in code**

The abandoned-upload sweep failed the row and left the bytes. It now aborts the
multipart upload and deletes any object a single PUT left — *before* failing the
row, so a cleanup that cannot reach storage leaves the row for the next sweep
instead of forgetting the bytes behind it. A bucket lifecycle rule is still
worth having in production; nothing depends on it.

### 2.5 M7-22 🟢 A container could name another local file — **reproduced, then fixed**

The register said a `file:` reference was "still followable". It was, and more
plainly than expected: **FFprobe picks the demuxer from the bytes, not the
name**, and an `ffconcat` script uploaded as `clip.mp4` was opened as concat
and its `file` line followed. With a `duration` line it passed `probe()` and
the proxy would have been made from the file next to it. Each job's scratch is
a private directory and concat refuses `..` and absolute paths, so nothing
outside the job was reachable — but that was a property of where files
happened to be, not a control. Now every FFmpeg call on user media names the
demuxers it may use (`-format_whitelist mov,matroska,avi,mp3,aac,wav,flac,ogg`,
one per container the upload route accepts), which refuses concat, HLS, DASH
and image sequences by name. The `movie=` source inside colour analysis gets
the same through `format_opts`, escaped for the three parsers that string
passes through and tested against the real one. One sample of every accepted
container is probed in the tests, so the list cannot quietly refuse a format
the product promises.

The first test of this passed **with the hole still open**: without a
`duration` line concat reports no duration, and `probe()` refused the file by
accident. The test file now carries the line, and fails without the fix.

### 2.6 M7-23 🟡 Self-referral and chargebacks had no policy — **a default, for the lead to confirm**

* **Self-referral.** No bonus, no attribution and no commission when the
  account is the code owner's own, or delivers to the owner's inbox (`+tags`
  dropped, Gmail dots ignored). Deliberately nothing wider: the same IP is a
  household as often as a fraud.
* **Refunds and chargebacks.** Razorpay's `refund.processed` and
  `payment.dispute.lost` were ignored events. They now mark the payment
  refunded and write a `REVERSAL` row cancelling that payment's whole
  commission — once, however many times the money goes back.
* **The hold.** Commission is *pending* for 30 days (`COMMISSION_HOLD_DAYS`),
  then *payable*; `GET /promo/{code}/stats` reports both. A reversal after a
  payout makes what is owed negative, and later commission nets it off.

Not decided here, and recorded for the lead: **does a refunded customer keep
the credits** granted for the payment? That is a support conversation, not a
ledger rule.

## 3. Part B, on a staging stack built on one machine

There is still no host. Rather than leave Part B untested, it runs against
[`deploy/local-staging/`](../deploy/local-staging/compose.yml): the
**production image** with `ENVIRONMENT=production`, RS256 tokens and generated
secrets, behind a Caddy proxy that terminates TLS for three origins
(`app.`, `api.`, `media.zipzop.test`). Two networks: `public`, where the proxy,
the frontend and the tester sit; and `data`, **internal** — no route out —
holding the API, the workers, Postgres, Redis (with a password) and storage.
The proxy is the only thing on both, so the workers' one way out is the storage
hostname. Storage is reached by an identity that can touch one bucket's objects
and nothing else.

```
make staging-up      # once: secrets, build, start
make staging-check   # 30 checks; SCANS=1 adds ZAP and sqlmap
```

| Area | Checked | Result |
|---|---|---|
| TLS | every origin over TLS 1.3; plain http only redirects (308); HSTS one year on all three | ✅ |
| Headers | nonce CSP on every page, fresh per response, 13/13 scripts nonced; `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Permissions-Policy`, no `X-Powered-By`; every API answer `nosniff` and `no-store` | ✅ |
| Production posture | `/docs`, `/redoc`, `/openapi.json` → 404; refresh cookie `HttpOnly; Secure; SameSite=Lax; Path=/v1/auth`, never in a body | ✅ |
| Network | Postgres, Redis, storage, API and worker unreachable from `public`. **Worker and API egress**: internet, `169.254.169.254` and PyPI closed; storage, Postgres, Redis open | ✅ |
| Redis | an unauthenticated client gets `NOAUTH` | ✅ |
| Rate limits | 45 logins each with a different forged `X-Forwarded-For` behind the real proxy: 429 by the 19th (M7-03 with `TRUSTED_PROXY_HOPS=1`) | ✅ |
| Storage | presigned PUT through TLS; one byte over the reservation 403; anonymous GET, listing, PUT 403; a URL bent to another key 403; an expired URL 403; CORS names the app and exposes `ETag`, a foreign origin gets nothing | ✅ |
| Storage identity (the IAM stand-in) | its own objects only; cannot see or touch another bucket, create one, delete this one or make it public | ✅ |
| Isolation | another account's upload: read, complete, delete all 404 | ✅ |
| WebSocket | a ticket opens one socket; the same ticket again, or an access token, 1008; the access log holds `ticket=REDACTED` | ✅ |
| The pipeline | a real video uploaded, completed, ingested by the worker over TLS, proxy and thumbnail fetched by presigned URL | ✅ |
| Signups | one address: ten an hour, then 429 for the rest of the hour (§5.2) | ✅ |

**Two things the stack found about itself**, both fixed there: the API runs as
uid 1000 and could not read the proxy's root certificate, so the first upload
to be *completed* through it 500'd — which is why the end-to-end ingest is now
a check of its own; and the image's `HEALTHCHECK` is the API's, so a worker
built from it reports unhealthy forever. **For the real deployment:** give the
workers their own health check, or none — an orchestrator acting on the
image's would restart a healthy worker mid-job.

### 3.1 The scans

`SCANS=1 make staging-check`, from the public network:

* **OWASP ZAP baseline** (`zaproxy/zap-stable`, pinned) on the app and the API:
  **no failure.** Its warnings that were worth acting on are fixed (M7-28, §5.5).
  Left, with reasons: `style-src 'unsafe-inline'` (documented in `csp.ts`);
  COEP and CORP (they would need the media origin to opt in, and nothing here
  uses cross-origin isolation); "non-storable content" — which is `no-store`
  doing its job — and "modern web application", both informational.
* **sqlmap** on the login body, both pagination cursors and the job filters,
  paced under the rate limits: **no injection on any of the eight
  parameters.** The first run was inconclusive on three targets — the login
  answers a wrong password with 401, which sqlmap takes for "stop", one path was
  wrong, and an unknown project is a 404 — so the script was fixed and the run
  repeated; that is the result above.

## 4. What only the real cloud can answer

Not passed — **untested**, because a laptop has none of these:

* **IMDSv2 required, hop limit 1** (§5.3). The staging workers cannot reach
  `169.254.169.254` at all, which is the stronger property; on AWS it has to be
  set, and checked from inside a task.
* **IAM roles**: the API's role cannot do what only the render worker needs,
  and neither can delete the bucket. Proved here for one storage identity, not
  for AWS's policy language.
* **The real ALB, S3 and CloudFront**: `X-Forwarded-For` as the ALB writes it
  (it appends; Caddy replaces — `TRUSTED_PROXY_HOPS=1` is right for both),
  the bucket policy and CORS as written in AWS, CloudFront's signed URLs.
* **A webhook delivered by Razorpay itself** — still only ever signed by us.
* The AWS testing policy, re-read the week the test runs (§2).

Each is one line in the deployment checklist below. None needs code.

## 5. What running Part B found

### 5.1 M7-24 🟡 One small upload could take a worker's whole machine — **fixed**

Twelve megabytes on disk: ten minutes of black 8192×8192 frames at 30 fps,
which compress to almost nothing and decode to 67 megapixels each. Uploaded
through the real flow by a free account, the production worker's FFmpeg took
**every core of the host (≈1,400% CPU) and 4.1 GB of memory for 322 seconds**
— on a 16-thread development machine. A 2-vCPU worker would need about seven
times as long, past the 15-minute FFmpeg timeout, and retry it. Two at once —
the worker's concurrency — is an out-of-memory kill on a typical 4–8 GB
instance, and everyone else's uploads wait behind it.

The cost of a file is pixels × frames, and its size says nothing about either.
Ingest now refuses a picture above 4096×4096 pixels — 4K in every orientation,
square included; export stops at 4K anyway — at the probe, which reads only
the header, before the proxy decodes a frame. Duration was already capped at
60 minutes, so pixels × frames is now bounded too. **Retested on the staging
stack with the same file: refused within 10 seconds**, with *"This video is
larger than 4K. Export it at 4K or below and upload it again."*

### 5.2 M7-25 🟡 One address could farm free accounts — **limited; email verification is the lead's call**

§6.10 asked what an abusive free account costs, and how many one address can
open. Measured through the real proxy: **20 a minute** — the shared auth limit
was the only one — so **1,200 an hour**, all on one disposable domain, each
with 300 credits and 5 GB. At the cost model in `job_costs.py` (about $3.28
for 800 credits) that is roughly **$1,500 of compute an hour** from one
address, before storage.

Sign-up now has a bucket of its own: **ten an hour per address**
(`REGISTER_LIMIT_PER_HOUR`), far above a household or a classroom and far below
a farm. It does not stop a farm with many addresses; email verification —
credits usable only after a confirmed inbox — is the control that does, and it
needs a mail provider. That decision was always meant to come from this number.

### 5.3 M7-26 🟡 CI had not passed since 30 August — **fixed**

[`23-m7-notes.md`](23-m7-notes.md) §2.1 could not tell from this machine whether
CI had been failing. It had, for five weeks, for three reasons in a row:

| From | Failing step | Cause |
|---|---|---|
| 30 Aug | `Test` | M5's Hindi caption tests need a Devanagari font; the runner had none |
| 6 Sep | `Lint` | M6 |
| 12 Sep | `Start MinIO` (`docker run` exit 125) | `minio/minio` and `minio/mc` deleted from Docker Hub — MinIO stopped publishing images. A machine with them cached never noticed; every fresh pull failed |

Pull requests #9, #11 and #12 were merged into `main` while it was red.

Fixed: **`pgsty/minio`**, Pigsty's maintained fork, pinned by digest in CI, the
compose file and `make pull` (the same server, so the signed-header behaviour
M7-04's tests assert is the same code); `fonts-noto-core` in CI as in the
production image; and one test made honest — the stuffed multipart part was
refused on Linux by a connection reset rather than a 403, so it now asserts
what matters, that **no part was stored**. The whole backend job was run in an
`ubuntu:24.04` container with apt's FFmpeg 6.1, as the runner does, before
pushing.

**The lesson is the branch protection**: a required green check would have
stopped all three merges.

### 5.4 M7-27 🟢 Killed workers left users' originals on disk — **fixed**

Every pipeline works in a temporary directory that removes itself when the
task ends — unless the process is killed, which is how workers usually end in
production (an OOM, a deploy, a scale-in). Three `docker kill`s during an
ingest left three directories, each holding the user's original upload, and
nothing ever removed them. The pipeline sweep (every five minutes) now removes
any `zipzop-*` directory nothing has written to for **six hours** — the longest
a live task goes silent is a 30-minute transcription, so this is safe whatever
the process topology. Verified on staging with a real orphan and the real task.

### 5.5 M7-28 🟢 What the ZAP baseline found — **fixed**

No failures on either origin; of the warnings, the ones worth acting on are
fixed: every API answer, errors included, now carries `X-Content-Type-Options:
nosniff` and `Cache-Control: no-store` (one account's data must not sit in a
shared cache); the app no longer announces `X-Powered-By: Next.js`, and sends a
`Permissions-Policy` refusing the camera, microphone and location it never
uses. Left, with reasons, in §3.1.

### 5.6 Also run

* **Cancel at the instant of success** (§6.6, the case M7 left open). Two
  tests that hold the row lock on purpose, one order each: a cancel that arrives
  while success commits gets 409 and no refund; a success that arrives while a
  cancel commits is dropped, and the job stays cancelled and refunded. A first
  version raced them freely and the worker won all eight rounds, so only one
  order was ever exercised — hence the locks. Removing either status guard fails
  its test.
* **The nightly ledger reconciliation** already logs `ledger_drift` at `error`
  with the accounts named. What M7-01 asked for — that it *alert* — is a log
  alarm on the host, in the checklist below.

## 6. The gates

CI's backend job now passes — lint, types, migrations, `alembic check`, every
test, the contract — in the `ubuntu:24.04` mirror. The `Security` workflow
had still never been *observed* on GitHub: it runs for the first time with this
push.

**M7-29 🟢 — the advisories published since 29 September.** `pnpm audit`, clean
on 29 September, reported ten on 5 October, all in build and lint tooling
(eslint, openapi-typescript, Next's eslint plugin) — none ships. Nine are
`brace-expansion`, fixed upstream on 14 September in every major line we pull
(1.1.21, 2.1.7, 5.0.12): forced with overrides, lockfile otherwise unchanged.
The tenth, **GHSA-vfj7-8cjw-p6xm in `braces` ≤ 3.0.3, has no fix — 3.0.3 is
the latest version.** It reaches us only through Next's eslint plugin, which
expands glob patterns from our own lint configuration. It is ignored by GHSA
id in `package.json` (`pnpm.auditConfig.ignoreGhsas`), with the reason beside
the audit step in `security.yml`, until 5 November 2026 or a fix, whichever
comes first. **That is a risk accepted instead of fixed, so under §8 it needs
the project lead's signature** — it is the only one in M7. This is also
exactly what the nightly run is for: these were published against code that had
not changed.

## 7. Owed by the project lead

> **Answered 6 October 2026 — §10.** Everything below is decided. The only
> thing still in the lead's hands is switching branch protection on, which
> only the repository's owner can do (§10.3).

0. **Sign or refuse the one accepted risk** — `braces`, §6.
1. **A disclosure address and a named person** for `security.txt`
   (`frontend/security.txt.template`).
2. **Branch protection on `main`**: pull requests only, the `CI` and `Security`
   checks required to pass. §5.3 is why.
3. **The patch policy** in [`23-m7-notes.md`](23-m7-notes.md) §5 — Critical
   within 3 days, High within 14.
4. **The referral policy** in §2.6 — confirm or change the hold, the reversal
   and the self-referral rule; and decide whether a refunded customer keeps the
   credits.
5. **Email verification at launch** — §5.2's number is the input.
6. **A host**, for §4.

## 8. The deployment checklist, complete

M7's settings ([`23-m7-notes.md`](23-m7-notes.md) §7), with today's added:

* `ENVIRONMENT=production`, RS256 keys, no development secret (the API refuses
  to start otherwise)
* `TRUSTED_PROXY_HOPS=1` behind one ALB
* `NEXT_PUBLIC_MEDIA_ORIGIN` = the CDN origin, or the CSP blocks every preview
* The frontend served by `next start` — pages render per request (the nonce)
* IMDSv2 required, hop limit 1; worker egress to S3 only — which means baking
  the `faster-whisper` model into the image
* S3: private, CORS to the app's origin exposing `ETag`,
  `AbortIncompleteMultipartUpload` after a day
* Redis with authentication, in a private subnet
* Workers with their own health check, not the image's
* Log alarms on `ledger_drift`, `billing_event_names_no_user`,
  `reversal_for_unknown_payment` and `pipeline_sweep_upload_cleanup_failed`
* Then: `make staging-check`'s list, re-run against the real hostnames, plus §4

## 9. Where to read next

| Read this | Before |
|---|---|
| `security/findings.md` (private) | Anything security-related — the register, with every reproduction |
| [`deploy/local-staging/`](../deploy/local-staging/compose.yml) | Deploying anywhere: it is the production topology, and `checks.py` is the acceptance test |
| [`services/ws_tickets.py`](../backend/app/services/ws_tickets.py) | Touching the socket or anything that puts a credential in a URL |
| [`services/ffmpeg_filters.py`](../backend/app/services/ffmpeg_filters.py) | Any new FFmpeg call on user media, or a new upload format — the format list must grow with it |
| [`tests/test_credits_race.py`](../backend/tests/test_credits_race.py) | Any test about concurrency or commits |

---

## 10. 6 October — the lead's answers, and what the first GitHub run found

### 10.1 The six decisions

| §7 | The project lead's decision | Where it landed |
|---|---|---|
| 0 | **`braces` accepted.** It is a lint-tool dependency and does not ship. | Unchanged in `package.json`: ignored by id until **5 November 2026** or a fix, whichever comes first. This row is the signature §8 asked for |
| 1 | **`security.txt`: `parthgiri95@gmail.com` for now**, the lead's own address and their choice, until Phase 2 brings a domain and a dedicated `security@` | [`frontend/public/.well-known/security.txt`](../frontend/public/.well-known/security.txt). It expires on **6 April 2027** on purpose, so the swap has a date |
| 2 | **Branch protection: yes** | [`.github/rulesets/protect-main.json`](../.github/rulesets/protect-main.json). Only the owner can apply it (§10.3) |
| 3 | **Patch policy: Critical and High within 24–48 hours, everything else within a week** | Replaces the 3-and-14-day proposal in [`23-m7-notes.md`](23-m7-notes.md) §5 |
| 4 | **Referral policy: a first violation (using your own code, or sharing it outside your server) cancels that commission and is a warning. A second removes the code** | `promo_violations` and `app.scripts.promo_violation` (§10.2) |
| 5 | **Email verification: not before launch.** It would add a mail service's cost and setup for a problem not yet seen. Revisit in Phase 2 if spam sign-ups appear | Sign-up stays limited to ten an hour per address (`REGISTER_LIMIT_PER_HOUR`, §5.2) |

Not answered, so they stay as the defaults of §2.6: the 30-day hold and the
reversal on refund or chargeback. Whether a refunded customer keeps the
credits was answered on 8 October: they do not (§10.7).

The lead said all of this is **temporary while pre-launch**: a business
address, a domain and the infrastructure behind them come with Phase 2.

### 10.2 The referral policy in code

A violation is **judged by a person**, because the API cannot see a code
shared outside a Discord server. The code is the bookkeeping that follows the
judgement:

    python -m app.scripts.promo_violation CODE --kind self_use --user buyer@example.com
    python -m app.scripts.promo_violation CODE --kind shared_outside_server --note "posted in …"

It is a dry run unless `--yes` is passed. For each violation,
[`services/promo_policy.py`](../backend/app/services/promo_policy.py) does this:

* It writes a row to **`promo_violations`** (migration `0009`), append-only:
  the kind, the account that came in through the breach, and what was done.
  "Second violation" needs the rows to be countable, and a warning has to be
  explainable months later to an owner who disputes it.
* **First violation:**
  * every accrual that account earned for the code gets a `REVERSAL` row,
    through the refund path's own `reverse_commission`, so a payment already
    refunded is never reversed twice;
  * the account is **detached** from the code. Otherwise its next renewal
    would accrue the same commission and the violation would keep paying.
    *This was the one reading that went beyond the lead's words. They
    confirmed it on 8 October (§10.7).*
* **Second violation:** the code is **retired** (`is_active = false`). A
  retired code grants nothing at sign-up and accrues nothing on any renewal,
  which is what "remove the code" has to mean. Commission legitimately earned
  before that stays owed.
* The **bonus credits** the customer got at sign-up are left alone: the policy
  is about the owner's commission.

Ten tests in [`tests/test_promo_policy.py`](../backend/tests/test_promo_policy.py)
pin down the money. The commission cancelled is only the breach's. A refunded
payment is not reversed twice, and neither is the same account reported twice.
An accrual whose payment row is gone is reversed once. A removed code stops
earning even on customers it brought in honestly.

### 10.3 Branch protection — one import, by the owner

On a repository owned by a personal account, **only the owner can manage
branch rules**; collaborators cannot. So it is prepared rather than applied:

1. **Settings → Rules → Rulesets → New ruleset → Import a ruleset**
2. Choose [`.github/rulesets/protect-main.json`](../.github/rulesets/protect-main.json), then **Create**.

On the default branch, it enforces this:

* every change arrives through a **pull request**. No approval is required,
  because the lead merges their own `dev` → `main` PRs and could not approve
  them;
* the **eight jobs of `CI` and `Security`** must pass, bound to the GitHub
  Actions app so another integration cannot post a fake green;
* **no force-push and no deletion**, with no bypass.

§5.3 is why: three PRs were merged while CI was red. One consequence is
intended: an advisory published overnight against a dependency turns a
required check red and blocks merging until it is fixed. That is the patch
policy's clock, made visible.

**Import it after the checks are green**, or the next `dev` → `main` PR is
blocked by the failures §10.4 fixes.

### 10.4 The first GitHub run was red

§6 said the `Security` workflow would run for the first time with the 5 October
push. It did, alongside `CI`, and three jobs failed. The logs need a signed-in
account, so each failure was reproduced locally with the job's exact images and
apt line:

* **M7-30 🟠 Every Free-plan export would have failed in production**
  (`backend`, six render tests).
  * **What happened.** The watermark is drawn with `drawtext`, which needs a
    font *file*, and `fonts.default_font()` looked for DejaVu, Liberation,
    Arial or Segoe. DejaVu used to arrive with `ffmpeg` through fontconfig's
    font dependency. But `fonts-noto-core`, installed in the image for Hindi
    captions and in CI since 5 October, **also satisfies that dependency**, so
    apt never installs DejaVu. The production image has
    `/usr/share/fonts/truetype/noto` and nothing else. Every watermarked
    render (the Free plan's are forced) raised `NoFontError`.
  * **Why the mirror missed it.** The 5 October mirror passed. Today's mirror,
    built from the job's exact `apt-get install --no-install-recommends ffmpeg
    fontconfig fonts-noto-core`, reproduces the runner's six failures. The
    difference is which fonts end up installed.
  * **Fixed.** Noto Sans now leads the fallback list. A test reproduces an
    image holding only Noto, and fails without the fix.
* **M7-31 🟡 Seven HIGH advisories in the production image's Debian
  packages** (`trivy (production image)`): OpenSSL CVE-2026-75804 and
  CVE-2026-84782 (`libssl3t64`, `openssl`, `openssl-provider-legacy`), and
  pcre2 CVE-2026-103111, all fixed in Debian after the 29 September base
  digest. The base image moved to the 6 October `python:3.12-slim` digest,
  which ships `3.5.7-1~deb13u3` and `10.46-1~deb13u3`. The rescan is clean.
  Under the new patch policy this is the 24–48 hour case.
* **`semgrep`** failed on GitHub, but was clean locally. The cause turned out
  to be this machine's line endings, not the rules (§10.5).

Local mirrors run on a Windows checkout see `openapi.json` "differ" from the
generated contract. Git's `core.autocrlf` gives the copy CRLF, and the content
is identical. A Linux runner does not have this.

The backend suite now has **535 tests**: 533 pass in the mirror, and 2 are
skipped because they need the cached Whisper model. That is 524 plus the 11
added here.

### 10.5 The second run, and why local scans had disagreed with GitHub

The 6 October push made `trivy (production image)` green and left three jobs
red. Two of them were new, from advisories published within the day. The last
needed a better mirror before it would reproduce:

* **`semgrep` had been right all along.** On LF files it reports
  `dockerfile.security.missing-user` on the **`dev`** stage's `CMD`. That
  stage is never deployed, and `prod` runs as `app`. Locally the scan was
  clean because this Windows checkout uses `core.autocrlf`: the Dockerfile
  arrived in CRLF, semgrep's Dockerfile parser stopped at the first `\`
  continuation (a `PartialParsing` warning, not an error), and the rule never
  ran. Every "semgrep clean" written from this machine since M7 had this blind
  spot. The fix:
  * the `dev` line is waived with `# nosemgrep` and the reason beside it. Its
    root user writes into the developer's bind-mounted checkout;
  * a root `.gitattributes` now gives `backend/Dockerfile` LF on every
    checkout, so local and GitHub scans read the same bytes;
  * mirrors now take `git -c core.autocrlf=false archive`, not the working
    copy.
* **`pnpm audit` and `trivy (lockfiles + configuration)`:** `source-map-js`
  < 1.2.2, GHSA-68fv-2mgg-jv7q / CVE-2026-93749 (HIGH, an event-loop DoS),
  reaches us through `postcss` from Next, Tailwind and Vite. It is fixed by an
  override to `^1.2.2`, in the same pattern as `brace-expansion`; the lockfile
  moves eleven lines. Frontend lint, types, 368 tests and the build all pass.
  This is the patch policy working as the lead set it: published, red, fixed
  the same day.
* **Test failures become annotations.** A job's log can only be read by
  someone signed in to GitHub, but a check run's annotations are public. On
  GitHub Actions, `tests/conftest.py` now writes each failing test as an
  `::error` annotation, with its last thirty lines.

### 10.6 The backend job: two async plugins, and a failure that moved

After §10.5, every job was green except `backend`, with three setup errors
the local mirrors never produced. The mirrors tried Python 3.12.3 and 3.12.14,
root and a non-root uid, LF files, and a single CPU. The new annotations gave
the first lead: pytest's own `assert not self._finalizers` in
`FixtureDef.execute`, on a test that **moved one place down** between runs.
A temporary tracer then named the cause:

    ScopeMismatch: You tried to access the module scoped fixture anyio_backend
    with a session scoped request object.

* **Two plugins ran async code.** `asyncio_mode = "auto"` makes
  pytest-asyncio run every async test and fixture. Fifteen modules *also*
  carried `pytestmark = pytest.mark.anyio`, a habit from FastAPI's docs, which
  switches on **anyio's** plugin for them. Both wrap async fixtures, and which
  one wins depends on the order their entry points load. That order is not
  the same on every filesystem.
* **On the runner, anyio won** for the session-scoped `engine`. It asked for
  its module-scoped `anyio_backend` and failed. The failure happened while
  pytest was still resolving `engine`'s arguments, before it caches anything.
  The finalizer `execute` had already registered stayed behind, and the next
  test to need `engine` died on that assertion rather than on the real error.
  That is why the failure showed up in an innocent test, one place later each
  run.
* **Fixed by having one plugin.** `-p no:anyio` in `addopts` (with the reason
  beside it), and the fifteen marks removed. Nothing used anyio's `trio`
  backend or `anyio_backend`. Test ids lose their `[asyncio]` suffix; the
  tests are the same. The tracer is gone. The annotations stay: they are what
  made a runner-only failure readable without a GitHub login.

**Result: `5301ec5` is green on all eight jobs** (`backend`, `frontend`,
`bandit + pip-audit`, `pnpm audit`, `gitleaks`, `semgrep`, and both
`trivy` jobs). It is the first fully green GitHub run since 30 August. The
branch ruleset (§10.3) can be imported now.

### 10.7 8 October — the lead's follow-ups

* **The detach is confirmed.** An account caught in a violation stops
  earning its referrer commission, as §10.2 implemented it.
* **A refund revokes the credits.** The lead: *"fully revoke credits on
  refund — if a customer already used some, just claw back whatever's left
  unused."* In code:
  * `refund.processed` and `payment.dispute.lost` now also call
    `CreditLedger.revoke_payment`, which writes a `payment_reversal` row
    (migration `0010`) per bucket. The amount is the smaller of what the
    payment granted and the balance left, so spent credits stay spent and no
    balance goes negative. A second event for the same payment revokes
    nothing.
  * A **subscription** month's grant rows now name the payment that bought
    them. Its credits are revoked only while that month is still the current
    one: once the next month has been granted, the refunded month's credits
    have already expired, and taking the same amount from the new month would
    charge for a period paid separately.
  * A **top-up** is revoked from `topup`, up to the pack it bought. A promo
    bonus in the same bucket is not touched.
  * The plan itself is not changed by a refund; the provider's cancellation
    event does that.
  * Seven tests in [`tests/test_billing_refunds.py`](../backend/tests/test_billing_refunds.py),
    all through a real Razorpay delivery. The suite is now **542 tests**: 540
    pass, and 2 are skipped because they need the cached Whisper model. The
    billing page labels the new rows *"Taken back: payment refunded"*.
* **Branch protection:** the lead imports the ruleset (§10.3) themselves.

---

*Build note · 5 October 2026, §10 added 6–8 October · M7 closed in code; the lead's decisions applied; the cloud-only checks wait for a host*
