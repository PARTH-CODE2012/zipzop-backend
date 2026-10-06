# M7 — the security review, and what running it found that reading had not

**28–29 September 2026.** Written against [`07-security.md`](07-security.md)
(the plan) and [`22-m7-readiness.md`](22-m7-readiness.md) (the map of the code
as built), in the order the readiness note suggested.

> **Closed on 5 October — [`24-m7-closure.md`](24-m7-closure.md).** Every
> finding listed as open in §6 below is fixed, Part B ran on a local staging
> stack, and three more findings came out of it. Read that note for the
> current state; this one is the record of 28–29 September.

This is the build note — what was decided, what was found, what the next phase
inherits. The findings themselves, with reproductions, live in the **private**
register `security/findings.md`, which is not in this repository (§4.1).

| | |
|---|---|
| Backend tests | **447 → 476**, every new one that pins a fix confirmed by reverting the fix and watching it fail — and the whole suite also run on a virtualenv built from nothing but the lockfile |
| Frontend tests | 352, now on vitest 4 |
| Findings | **17 fixed** — 2 Critical, 3 High, 7 Medium, 4 Low, 1 informational · **6 open**, none Critical or High |
| Gates | bandit · pip-audit · pnpm audit · gitleaks · semgrep · trivy, in `.github/workflows/security.yml` and `make security` |
| Launch gate (§8) | 🟢 no open Critical or High · 🔴 **M7 cannot close**: Part B's deployment targets were never tested — there is no staging (§6) |

> **The honest framing, from §1 of the plan and worth keeping in front of any
> reader.** This is a review of one's own code by the person who wrote it. It
> found two Critical defects that three milestones of careful work had not —
> which is the argument for doing it, and also for an **external** test once
> there is revenue to pay for one. Nothing below claims to be that.

---

## 1. The readiness note, checked against the running system

The note ranked six things by reading. Running them changed the order.

**Its first item was the one that did not reproduce.** FFmpeg with no pinned
protocol allowlist was ranked as a potential Critical: an uploaded HLS playlist
pointing at the instance metadata endpoint. Tried on both FFmpeg builds that
matter — 9.0.1 on the development machine, 7.1.5 in the production image — the
demuxer's compiled-in default (`file,crypto,data`) refused `http` before our
code was involved, and the concat demuxer's `-safe` default refused the other
vector. The allowlist is now pinned anyway, at all ten call sites, because a
default that varies by build is not a guarantee; but it was hardening, not a
hole, and it is recorded as Medium on that evidence.

**The Critical nobody predicted was in the money path.** §6.6 of the plan names
the attack exactly — *"fire 20 concurrent `POST /jobs` against a balance of
one"* — and nothing had ever done it. Doing it created **twenty jobs**. The
`SELECT … FOR UPDATE` was there and did take the lock; but the request had
already loaded the `User` through `current_user`, and SQLAlchemy returns an
identity-mapped object without re-reading its columns. Every request priced
itself against a balance read before it waited, and wrote `stale - cost` back.
One argument — `populate_existing=True` — fixes it. The same trap
`claim_for_ingest` had documented and avoided in August; it was not carried to
the one place it mattered most.

**A docstring was defending the bug.** `client_ip` said the first
`X-Forwarded-For` entry was trustworthy *because* the service sits behind a
load balancer. A load balancer appends; the first entry is the client's. One
header per request bought a fresh brute-force allowance on `/auth/login`.

**Three of the six predictions held as written** — the unthrottled catalogue,
the missing CSP, the unguarded watermark sink — and were closed as the note
proposed. The fourth, the M6 routes that filter by hand instead of through a
`ScopedRepository`, held too: every hand-written filter checked out, including
under a forged ledger cursor, which is now a test.

## 2. The findings

Summarised here at the level the code comments already describe them; the
register has the reproductions.

| | Finding | Fix |
|---|---|---|
| 🔴 | Twenty concurrent jobs against a balance of one all succeed | `populate_existing` on the locking select; `tests/test_credits_race.py` |
| 🔴 | `next@15.5.23` — two unauthenticated RCE advisories | Next 15.5.26; image optimisation off (`/_next/image` → 404; unused) |
| 🟠 | Rate limits bypassed by forging `X-Forwarded-For` | Counted from the right, `TRUSTED_PROXY_HOPS` |
| 🟠 | Upload size unsigned; a rejected oversize object stayed in `originals/` forever, outside every quota | `Content-Length` signed into single and part URLs; the rejected object is deleted |
| 🟠 | Development Postgres, Redis and MinIO published on every interface — IPv4 **and** globally routable IPv6 — with the credentials in this public repository | Loopback only, both families |
| 🟡 | FFmpeg allowlist implicit (§1) | `-protocol_whitelist file`, and `format_opts` on the lavfi `movie=` source, which the flag does not reach |
| 🟡 | Webhook body read without a ceiling before its signature can be checked | 1 MB, counted on the stream |
| 🟡 | A retried idempotency key returned 500 under concurrency | The replay check repeated behind the lock |
| 🟡 | `NaN`/`Infinity` crashed the 422 handler into a 500 | Non-finite values stringified in the error envelope |
| 🟡 | No Content-Security-Policy | CSP from the deployment's own origins, `frame-ancestors 'none'` |
| 🟡 | No Python lockfile | Hashed, universal lockfiles; CI, the image and `make install-backend` install from them |
| 🟡 | Actions on mutable tags, workflow token not read-only | Pinned to verified SHAs, `contents: read` |
| 🟢 | Development-only advisories (vitest, vite, esbuild, postcss, sharp, js-yaml) | Upgrades and `pnpm.overrides` |
| 🟢 | `/catalog/luts` unthrottled · watermark text a parameter · dev tools and pip in the production image | Limiter · a constant · removed |
| ⚪ | `/docs` and `/openapi.json` served in production | Not served when `ENVIRONMENT=production` |

### 2.1 🔴 The lockfile found a bug on its first build

Building the production image from the new lockfile produced a container that
**could not import the API**: `No module named 'greenlet'`. SQLAlchemy 2.1
stopped installing it on its own; the dependency is `sqlalchemy[asyncio]`. The
development virtualenv still held greenlet from 2.0, so every test passed.

This was not introduced by the lockfile. `pyproject.toml` has only ever stated
ranges, and CI ran `pip install -e ".[dev]"` fresh on every run — so any clean
install since SQLAlchemy 2.1 appeared resolved to a version that cannot start
the async engine. Whether CI had been failing on it is not visible from this
machine. It is the lockfile argument in one line: the environment that was
tested was not the environment a fresh install produces, and nothing said so.

The suite now also runs against a virtualenv built from nothing but the lock.
SQLAlchemy 2.1 also changed its type annotations, which surfaced four `mypy`
errors in code that had not changed — fixed, and a second sign that CI and the
development machine had not been running the same libraries.

### 2.2 The image scan saw what the dependency audit could not

`pip-audit` on both lockfiles: clean. `trivy` on the built image: two HIGH
advisories, in `msgpack 1.1.2` and `setuptools 70.3.0` — neither of which is a
dependency of ours. They were **pip's own vendored copies**, declared in the
SBOM pip ships. Nothing at runtime runs pip, so the risk was small; the answer
was still to remove pip from the production stage rather than ignore the
advisory. A production container with no package manager is also one where an
intruder cannot `pip install` their tooling.

### 2.3 Verifying the network fix broke the network

Binding the development ports to `127.0.0.1` made `localhost` — which resolves
to `::1` first on Windows — hang in `SYN_SENT` until the TCP timeout. Every new
database connection took tens of seconds, and the suite crawled for eleven
minutes. The fix publishes both loopbacks; the compose file says why, so the
`[::1]` lines are not removed as redundant.

## 3. The lesson this suite keeps teaching

[`21-m6-notes.md`](21-m6-notes.md) §2.2 ended: *"any request-scoped transaction
behaviour is invisible to this suite."* M7 found the second instance, and it
was worse than the first. The `client` fixture runs every request in one
savepoint on one connection, so **two requests cannot race** in any test that
uses it — and `test_credits.py` had described the concurrency property in its
docstring for a month without exercising it.

`tests/test_credits_race.py` is the harness for the next one: an app whose
sessions are shaped the way production shapes them, one real connection each,
committed for real and cleaned up by hand. Two warnings from building it:

* its data is committed, so a cleanup that fails poisons every later test that
  scans globally (the pipeline sweeps did, until the leftovers were removed);
  the cleanup retries a lost deadlock for that reason;
* a failing request can still be tearing its transaction down when the test's
  cleanup starts, and Postgres picks the cleanup as the deadlock victim — whose
  error then replaces the real assertion in the report.

**Anything concurrent, and anything whose correctness depends on a commit,
belongs in that shape of test.**

## 4. What was decided

### 4.1 The register is private, and this repository is public

The plan (§2, §9) requires findings and proof-of-concept payloads to live in a
private register. This repository answers unauthenticated GitHub API calls, so
it is public: `security/` is gitignored. **Keep a copy of
`security/findings.md` somewhere private** — a private repository or a password
manager's secure notes. It is the only record of the reproductions.

### 4.2 The CSRF story

Contract 1.2 made refresh a cookie, which is what made a CSRF story necessary
(§5.2). Decided: state-changing routes authenticate with a bearer header, which
a cross-site page cannot send. The two cookie-authenticated routes — `refresh`
and `logout` — take a `SameSite=Lax` cookie scoped to `/v1/auth`, which a
cross-site `POST` does not carry. No token is added. The residual is a hostile
page on a *sibling subdomain*, which is same-site: keep the product's
registrable domain to the product.

### 4.3 Numbering

The plan named this note `docs/08-m7-notes.md`. `08` became the UI charter on
17 August; build notes are numbered in order, so this is `23`.

## 5. The gates that stay

`.github/workflows/security.yml` — every push, every pull request, and nightly,
because an advisory published tonight against unchanged code is only seen by a
schedule. `make security` runs the same scanners from the same pinned images.

| Tool | Reads | Result at M7 |
|---|---|---|
| **bandit** | `backend/app` | Clean · four check ids skipped with a reason each in `pyproject.toml`, two handled inline |
| **pip-audit** | both lockfiles, pinned and hashed | No known vulnerabilities |
| **pnpm audit** | `pnpm-lock.yaml` | No known vulnerabilities |
| **gitleaks** | all 55 commits on every ref | No leaks |
| **semgrep** | Python, OWASP Top 10, React, TypeScript rulesets · 252 files | Clean once the actions were pinned |
| **trivy fs** | lockfiles, Dockerfile, secrets | Clean |
| **trivy image** | the production image, built in the job | Clean once pip was removed (§2.2) |

`make lock` regenerates the Python lockfiles after `pyproject.toml` changes.
**The patch policy (§5.4)** — **set by the project lead on 6 October 2026**
([`24-m7-closure.md`](24-m7-closure.md) §10.1): a **Critical or High** advisory
in anything that ships is fixed within **24–48 hours**, everything else
within **a week**. That is stricter than the 3-and-14 days first proposed here.
The nightly run is what starts the clock, and once branch protection is on, a
required check turning red is the clock made visible.

**The fuzz run (§6.4)** — fifteen minutes of random header mutation over MP4,
MOV, MKV, WebM and WAV, against the production image's `ffprobe` exactly as
ingest calls it, in a container with no network: **10 210 cases, no crash, no
hang.** That is weak evidence and is recorded as such — undirected mutation, a
quarter of an hour, and the probe only; the full decode `make_proxy` runs is a
larger surface nobody has fuzzed. It gives no reason to get ahead of Debian's
FFmpeg updates. It does not settle §6.4's sandboxing question, which rests on
worker egress and therefore on staging.

## 6. 🔴 What M7 did not do, and cannot until something outside the code changes

**There is no staging environment.** Everything in Part B that depends on a
deployment is untested — not passed, *untested*: IMDSv2 and its hop limit,
worker egress, IAM boundaries, the real bucket's policy and CORS, TLS and HSTS,
Redis authentication in production, `X-Forwarded-For` behind the real ALB, a
webhook delivered by Razorpay itself, and §6.10's measurement of what an
abusive free account costs — the number that decides whether email
verification ships at launch. **M7 closes when those are run**, not before.

**Open findings**, none blocking under §8:

| | What | Owner |
|---|---|---|
| 🟡 | The WebSocket token rides in the URL; access logs record it | Deployment — the load balancer's log format |
| 🟡 | Self-referral and a chargeback after a commission is paid have no policy | **Project lead**, before the first payout |
| 🟢 | An open socket outlives its access token; closing it needs the client to reconnect with a fresh one, or live progress stops after 15 minutes | Developer |
| 🟢 | `script-src 'unsafe-inline'` until there is nonce middleware | Developer |
| 🟢 | Abandoned multipart uploads are never aborted | Deployment — a bucket lifecycle rule |
| 🟢 | A `file:` reference inside a container is still followable; `file` must stay allowed to open the upload | Developer + deployment — the fuzz run, and egress |

**Also owed, and not code:**

* **`security.txt`** needs a disclosure address and a **named person** who
  answers it. `frontend/security.txt.template` is ready apart from those.
* **Branch protection on `main`** — a GitHub setting, owned since 17 August.
* **Accepting the patch policy** in §5, and any risk accepted instead of fixed,
  in writing (§8).
* **An external penetration test** once there is revenue (§11).

## 7. The deployment checklist M7 leaves behind

Settings that are safe by default in development and wrong in production if
forgotten:

* `ENVIRONMENT=production` — refuses dev secrets and test keys, hides `/docs`
* `TRUSTED_PROXY_HOPS=1` behind one ALB — otherwise every user shares one
  rate-limit bucket
* `NEXT_PUBLIC_MEDIA_ORIGIN` = the CDN origin — otherwise the CSP blocks every
  preview
* The load balancer's access log must not record the WebSocket query string
* S3 lifecycle: `AbortIncompleteMultipartUpload` after one day; CORS exposing
  `ETag` to the app's origin only
* IMDSv2 required, hop limit 1; worker egress to S3 only — which means baking
  the `faster-whisper` model into the image rather than downloading it on first
  use

## 8. Where to read next

| Read this | Before |
|---|---|
| `security/findings.md` (private) | Anything security-related — it is the register, and it keeps being used after M7 (§10) |
| [`tests/test_credits_race.py`](../backend/tests/test_credits_race.py) | Writing any test about concurrency or commits |
| [`services/credits.py`](../backend/app/services/credits.py) `lock_user` | Adding a new place that locks a row the request already loaded |
| [`services/ffmpeg_filters.py`](../backend/app/services/ffmpeg_filters.py) | Adding any FFmpeg call on user media — §10: every new media tool gets its own short review |
| [`.github/workflows/security.yml`](../.github/workflows/security.yml) | Silencing a scanner — every skip there has a written reason, and a new one should too |

---

*Build note · 29 September 2026 · M7 fixed everything it found; it cannot close until there is a deployment to test*
