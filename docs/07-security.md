# Security Review and Penetration Testing — M7

**What this document is.** The plan for the last milestone before launch: a full
review of the code and the running system, followed by a deliberate attempt to
break it. It says what is in scope, what is off limits, what gets attacked, what
counts as a finding, and what blocks the release.

Read it before starting M7. Skim §2 and §8 before starting M3 — a few of the
decisions taken between here and there are cheaper to take correctly than to
undo under a finding.

| | |
|---|---|
| **Milestone** | M7 — *"someone tries to break it on purpose, and we fix what they find"* |
| **Runs after** | M6. Everything phase 1 ships must exist before it is reviewed |
| **Ends when** | No critical or high finding is open, every fix has a regression test, and the automated gates are running in CI |
| **Checklist** | [`../PHASE1-TASKS.md`](../PHASE1-TASKS.md) · M7 |
| **Existing controls** | [`03-backend-architecture.md`](03-backend-architecture.md) §10 — this document tests those claims rather than restating them |

---

## 1. Why this is a milestone and not a checklist item

Security work that lives as a line inside every other milestone gets done at the
depth of whoever was in a hurry that week. Three things here need a block of
uninterrupted time and a different frame of mind:

**1 · The system only exists as a whole at the end.** The interesting attacks
cross components — a presigned URL issued by the API and consumed by S3, a
credit reserved by a route and refunded by a worker, a caption written by a
model and rendered by FFmpeg. None of them can be tested while half the pipeline
is a stub.

**2 · Building and attacking are different jobs.** Writing code asks "does this
work?". Reviewing it asks "what did I assume?". Doing both in the same hour means
doing the second one badly.

**3 · We take people's unpublished footage and hold their money.** Raw video is
often the most sensitive thing a creator owns, and credits are money in a table.
Both deserve a deliberate pass rather than a green test suite.

> **The known weakness of this plan.** Phase 1 is built by one developer, so M7
> is a review of one's own code by the person who wrote it. That is worth less
> than an outside review, and pretending otherwise helps nobody. Three things
> reduce the cost: work from the checklist rather than from memory (memory
> defends its own decisions), let tools cover the mechanical half, and budget an
> external test once there is revenue to pay for one — see §11.

---

## 2. Rules of engagement

Agreed before anything is attacked. These are not formalities: the difference
between a penetration test and an incident is that someone approved the first
one.

| | Rule |
|---|---|
| **Where** | A dedicated **staging** stack only, deployed from the release commit. Never production |
| **Data** | Synthetic accounts and synthetic media, generated the way `make e2e-media` does. **No real user footage, no real card details, ever** |
| **Payment providers** | Stripe and Razorpay **are never targets.** Test against their sandboxes with their test events. The thing under test is our verification code, not theirs |
| **AWS** | Customer testing of one's own resources is permitted for a published list of services without asking, but anything resembling a denial-of-service test is not covered and needs a separate request. **Read the current policy the week the test runs** — it changes |
| **Third parties** | GitHub, Docker Hub, the model vendor, CloudFront: out of scope. A finding that involves one gets reported to them, not exercised |
| **People** | No phishing, no social engineering, no physical access attempts. On a team this size it produces noise, not information |
| **Denial of service** | Rate limits and resource ceilings are tested to the point of *demonstrating* the limit, not past it. "How much does an abusive free account cost us" is a measurement (§6.10), not a flood |
| **Evidence** | Findings and proof-of-concept payloads live in a **private** register (§9), never in a public issue or a screenshot in chat |
| **Real credentials** | Any live secret found is rotated within the hour and recorded as rotated. It is never pasted into the register |
| **Stop** | The project lead can halt the test at any point. Anything that reaches production by accident is an incident and is handled as one, immediately and in writing |

---

## 3. Scope

| Surface | In | Notes |
|---|---|---|
| The API — every route in [`05-api-contract.md`](05-api-contract.md) | ✅ | Including the ones a client never calls |
| The WebSocket `/ws` and its Redis fan-out | ✅ | Authorisation at connect *and* per subscription |
| The workers — ingest, analysis, render | ✅ | The ingest worker is the highest-value target in the system (§6.4) |
| Object storage, buckets, presigned URLs, CORS | ✅ | Both the API's use of them and direct access |
| The database, its migrations, its grants | ✅ | Including what a compromised API replica can reach |
| The frontend as shipped | ✅ | Headers, cookies, XSS paths, redirect handling |
| Infrastructure configuration | ✅ | Security groups, IAM policies, secrets handling, container hardening |
| Dependencies and the build path | ✅ | Both trees, the images, CI permissions, branch protection |
| Payment providers' own systems | ❌ | §2 |
| AWS control plane and shared services | ❌ | Our configuration is in scope; their platform is not |
| Staff, offices, devices | ❌ | §2 |

---

## 4. Threat model — what is actually worth attacking

### 4.1 Assets, ranked

| # | Asset | Loss looks like |
|---|---|---|
| **1** | **Users' raw footage** | Unpublished, often commercially sensitive, occasionally personal. One cross-account read is the worst thing that can happen to this product |
| **2** | **Account access** | Passwords, the 30-day refresh token, session takeover |
| **3** | **Money** | Credits are money in a table. A race in `allocate()` is not a bug class, it is theft; a webhook forgery is a free subscription |
| **4** | **Compute** | The job queue is free compute for anyone who can make it run their work. Cryptomining through a crafted job is an economic attack with no data breach attached |
| **5** | **Availability** | An abusive free tier that costs more than it earns is an outage with a bill |
| **6** | **Data integrity** | A ledger that cannot be reconciled cannot be trusted afterwards, even once the hole is closed |

**No biometric or facial data exists in phase 1** — deliberately, per
[`02-scope-v1.md`](02-scope-v1.md) §5. It is not on this list because it is not
in the database. It becomes the first item on the phase 2 list, which is a
different review (§11).

### 4.2 Actors worth modelling

- **The unauthenticated internet** — everything reachable before login.
- **A signed-up free user.** The most realistic attacker in this product, because becoming one costs an email address. Most of §6 is written from this position.
- **A paying user**, who additionally reaches billing, export and higher limits.
- **A hijacked browser session** — the XSS-succeeded case. What can it still not do? (This is what the httpOnly refresh cookie is for; verify it delivers.)
- **A malicious media file.** Not a person: a file. It crosses the boundary into FFmpeg with no human in the loop.
- **A hostile webhook sender**, posting to the billing endpoints with a guessed or replayed signature.
- **Whoever holds a leaked presigned URL** — an old log line, a shared link, a browser history.

### 4.3 Trust boundaries

Each of these is a place where data changes hands and assumptions stop holding.
Every finding in §6 sits on one of them.

```
browser ──────► API            authn, authz, input validation, rate limits
API ──────────► Postgres       ownership filters, injection, least privilege
API ──────────► Redis          queue, rate-limit keys, idempotency keys
API/worker ───► S3             presigned URL scope, bucket policy, CORS
worker ◄─────── media file     ⚠️ untrusted parser input — the sharp one
worker ───────► filter graph   user text and timeline values into FFmpeg
API ◄────────── provider       webhook signature, replay, event ordering
CI ───────────► production     branch protection, deploy credentials, image supply
```

---

## 5. Part A — the code review

Reading, not attacking. Done first: it is what tells §6 where to aim.

### 5.1 Backend

- [ ] **Every route, one at a time.** Is it authenticated? Does it enforce ownership at the repository, not the handler? Can it be reached with another user's identifier?
- [ ] **The `ScopedRepository` claim.** M2's structural argument — that a cross-user query cannot be expressed — is only true for the repositories that use it. Verify every repository added in M3–M6 still does, and that no route builds a query around it. [`backend/app/repositories/base.py`](../backend/app/repositories/base.py)
- [ ] **Auth end to end** — token lifetimes, rotation, reuse-revokes-the-chain, logout actually revoking, the equal-time login path, bcrypt cost, and the SHA-256 pre-hash (confirm it introduces no truncation or shucking issue). [`backend/app/services/security.py`](../backend/app/services/security.py)
- [ ] **Validation at the edge.** Pydantic on every body, `app/api/ids.py` on every identifier, no `dict[str, Any]` reaching a query
- [ ] **The timeline validator is a security control**, not only a correctness one — it is the parser for attacker-controlled JSON that M5's renderer later turns into a filter graph. All eight invariants from [`05-api-contract.md`](05-api-contract.md) §4.3, plus bounds on every numeric field
- [ ] **SQL.** SQLAlchemy constructs only; no f-string SQL, no `text()` carrying interpolation
- [ ] **Secrets.** `assert_production_safe()` really refuses to boot on dev defaults; `gitleaks` over the **full history**, not the working tree
- [ ] **Logs leak nothing** — no passwords, no tokens, no signed URLs. A presigned URL in a log line is a credential with an hour to live
- [ ] **Error envelope** returns no stack trace, no SQL, no internal identifier in production mode
- [ ] **Idempotency and rate-limit keys** are derived from something an attacker cannot rotate for free
- [ ] **Money paths read from the server's own rows.** No price, duration or credit cost is ever taken from the request body

### 5.2 Frontend

- [ ] **No token in `localStorage`.** The refresh cookie is httpOnly, `Secure`, `SameSite`, correctly scoped
- [ ] **CSRF.** Contract 1.2 made refresh a cookie, which is what makes a CSRF story necessary. Every cookie-authenticated state-changing route needs one — decide and document whether it is `SameSite=Strict`, a token, or both
- [ ] **XSS paths.** Filenames, project names, caption text — anything a user or a model wrote. No `dangerouslySetInnerHTML` anywhere near them. Text drawn into a canvas is not an injection path; the same string in the media bin is
- [ ] **CSP, `frame-ancestors`, `Referrer-Policy`, `X-Content-Type-Options`**, and no inline script that forces `unsafe-inline`
- [ ] **Bucket CORS is narrow.** M2 fixed the compositor by setting `crossOrigin` on the video element; confirm the fix on the storage side is an origin list and not `*`
- [ ] **User media is not served from the application origin.** A file the user uploaded must not be able to run as our first-party script
- [ ] **The checkout return URL is not attacker-controllable** — an open redirect on the billing return path is a ready-made phishing primitive against our own users

### 5.3 Infrastructure and configuration

- [ ] Every bucket private, no public listing, lifecycle rules as documented in [`03-backend-architecture.md`](03-backend-architecture.md) §6.3
- [ ] Postgres and Redis unreachable from the internet. **Redis requires auth** — an open Redis is the classic hole, and ours holds the queue
- [ ] IAM: the API's role cannot do what only the render worker needs, and neither can delete a bucket
- [ ] Containers run non-root, with a read-only filesystem where possible and **no Docker socket mounted**
- [ ] IMDSv2 required, hop limit 1 — this is half of the SSRF defence in §6.4
- [ ] Secrets in Secrets Manager; **no long-lived AWS keys in CI** — OIDC
- [ ] TLS everywhere, HSTS on, no plaintext listener
- [ ] **Branch protection on `main`** — still unticked in M0, and it is a supply-chain control, not an administrative nicety
- [ ] Worker egress restricted: the ingest worker needs S3 and nothing else. Verify it, then try to prove otherwise from inside (§6.4)

### 5.4 Dependencies and supply chain

- [ ] Lockfiles committed on both sides, and CI installs from them
- [ ] `pip-audit` and `pnpm audit` clean, or every exception written down with a reason and a date
- [ ] Container images scanned; base images pinned by digest
- [ ] **FFmpeg version pinned and current.** It is the component that parses hostile input for us, so its CVE feed is our CVE feed
- [ ] A stated patch policy: how many days a critical advisory has before it must be deployed
- [ ] GitHub Actions pinned to a SHA, not a moving tag; workflow permissions read-only by default

---

## 6. Part B — the penetration test

Each subsection is a target, a set of attempts, and what would count as proof.
Findings go straight into the register (§9) with a reproduction, not a
description.

### 6.1 Authentication and sessions

Register and log in as two users and hold both sessions throughout. Then: reuse
a rotated refresh token (the chain must die), use a refresh token after logout,
use an access token after the account is disabled, forge a JWT with `alg: none`
and with the public key as an HMAC secret, replay a token across accounts, and
enumerate users through timing or response differences on login, register and
password reset. Check the 15-minute expiry is actually enforced server-side and
not just believed by the client.

### 6.2 Multi-tenant isolation

**The most damaging finding available in this product, so it gets the most
time.** With user B's session, request every one of user A's identifiers:
project, asset, job, ledger entry, subscription, payment, WebSocket
subscription. Then the indirect paths — a project referencing another user's
asset id, a job created against someone else's asset, a duplicate of a project
that is not yours, a cursor from another account's page. M2 proved this for the
media endpoints against two accounts; M7 proves it for everything M3–M6 added.

**Proof required:** for each resource type, a 404 or 403 with no distinguishing
detail between "does not exist" and "is not yours".

### 6.3 Object storage and presigned URLs

Anonymous GET on a proxy, a thumbnail, a peaks file and an original — all must
403 (M2 fixed exactly this once already). Reuse a presigned PUT after its 15
minutes. Use a PUT issued for one key to write another. **Upload 5 GB through a
URL issued for a 5 MB file** — if the quota is enforced only at
`POST /media/uploads`, the content-length condition on the signature is the only
thing standing behind it. Change the content type against a signature that
covers it. Overwrite an existing object. Read a bucket listing. Try key
traversal through a crafted filename.

### 6.4 The ingest worker — untrusted media into FFmpeg ⚠️

This is the sharpest boundary in the system: a file chosen entirely by an
attacker is handed to a C parser with no human in between. `subprocess.run` is
called with an argument list and a timeout, so shell injection is not the
concern — the container formats are.

| Attempt | What it would prove |
|---|---|
| An HLS `.m3u8`, a `concat` demuxer script, or a `.mov` with an external data reference pointing at `http://169.254.169.254/` or `file:///etc/passwd` | **SSRF or arbitrary file read from inside the worker.** The fix is an explicit `-protocol_whitelist file` on every invocation, plus IMDSv2 (§5.3) |
| A file declaring a 100 000 × 100 000 frame, 10 000 streams, or a 400-hour duration | Memory and disk ceilings hold, or the worker dies and takes the queue with it |
| Fifty of those uploaded at once by one free account | Per-user concurrency caps apply to ingest, not only to paid jobs |
| A filename of `../../etc/cron.d/x`, a null byte, 4 kB of Unicode | Path traversal into the S3 key or the temp directory |
| A shell script named `holiday.mp4` with `Content-Type: video/mp4` | It is rejected on probe, and is never served back with the type the client declared |
| Kill the worker mid-job, repeatedly | Temp files are cleaned up; disk does not fill over a week of failures |
| From inside the worker container, reach the internet, the database and another service | Egress restrictions are real (§5.3) |
| A short **fuzz** run: mutated MP4/MOV/MKV/WAV headers against `ffprobe`, a few hours, crash-triage only | Crash signal on our pinned FFmpeg build. Timeboxed deliberately — we are looking for a reason to upgrade or sandbox, not writing an exploit |

> If any of these lands, the answer is not only a patch: it is whether ingest
> should run in a tighter sandbox — seccomp, no network namespace, a separate
> account. Decide that during M7, while the evidence is in front of you.

### 6.5 The export renderer — user text into a filter graph

M5 builds one FFmpeg filter graph from a document the user controls. Caption
text reaches `drawtext`, where `:`, `\`, `'`, `%` and newlines all mean
something. Attempt: a caption containing filter-graph syntax, a caption naming a
font file path, a speed of `0`, a negative crop, a NaN, a 1e9 duration, a LUT
name that is a path. **Assert the graph is assembled by a structured builder
with escaping in one place, never by string concatenation, and that text is
passed by file rather than inline where the format allows it.**

### 6.6 Credits and the ledger — the money bugs

Unique to this product and invisible to every generic scanner.

- Fire 20 concurrent `POST /jobs` against a balance of one. **The ledger must never go negative**, and exactly one job may be created.
- The same, all carrying one idempotency key. Then the same key with a different body.
- Cancel a job at the moment it succeeds. Force a failure after the result is written. Both must settle once.
- Trigger the period-rollover case deliberately — the documented edge where a refund goes to `topup` instead of `plan`.
- Send a client-computed estimate, duration or credit cost and see whether anything believes it.
- Reconcile afterwards: reserved = settled + refunded, per bucket, across the whole test run. A drift of one credit is a finding.

### 6.7 Billing webhooks

Post an unsigned event. Post one with a valid signature and a tampered body.
Replay a valid event twice (the primary key must drop the second). Post an event
for another user's subscription. Post events out of order — cancel before
create. Post a 10 MB body. Post an event naming a plan that does not exist, and
one granting a credit amount we never sell. Confirm the documented order holds:
**verify → store → 200 → process async**, and that a processing failure never
turns into a 500 that makes the provider retry forever.

### 6.8 Jobs, queue and WebSocket

Subscribe to another user's job id. Connect without a token, with an expired
one, and with one that expires mid-connection. Cancel someone else's job. Create
a job against an asset that is not yours, a project that is not yours, or a tool
that does not exist. Check progress events carry nothing about other users, and
that the reconnect path (`GET /jobs?status=running`) is scoped the same way as
everything else.

### 6.9 The browser

XSS attempts into project names, filenames, caption text and any field a model
writes; both stored and reflected. CSRF against `/auth/refresh` and every
cookie-authenticated mutation. Clickjacking on the billing pages. Open redirect
on the checkout return. Then the post-XSS question, which is the interesting
one: **with script execution on our origin, can the attacker extract the refresh
token?** If the answer is anything but no, contract 1.2's whole justification is
wrong and the finding is critical.

### 6.10 Rate limits and the cost of abuse

Bypass the limiter by spoofing `X-Forwarded-For` (what does the app trust behind
the ALB?), by rotating accounts, over the WebSocket, and by going straight to
the presigned PUT instead of through the API. Then **measure**: how many
accounts can one IP and one disposable-mail domain create in an hour, and what
does each of them cost us in storage and compute before paying anything? That
number belongs in the finding register whatever it is — it is the input to
whether the free tier needs email verification before launch.

---

## 7. Tooling

Automated tools cover the mechanical half. They will not find the ledger race,
the presigned-URL bypass or the filter-graph injection — those come from reading
the code with §4.2's actors in mind.

| Tool | Finds | Runs |
|---|---|---|
| **semgrep** (python, fastapi, react, owasp rulesets) | Injection patterns, unsafe sinks, missing authz decorators | CI, every PR — **stays after M7** |
| **bandit** / ruff `S` rules | Python security anti-patterns | CI, every PR — stays |
| **pip-audit**, **pnpm audit**, **osv-scanner** | Known advisories in both trees | CI daily — stays |
| **gitleaks** | Secrets, working tree and full history | CI, every PR — stays |
| **trivy** | Image CVEs and IaC misconfiguration | On image build — stays |
| **OWASP ZAP** baseline | Headers, obvious injection, missing controls | Staging, M7, then before each release |
| **Burp Suite** (manual) | Everything in §6 that needs a human | Staging, M7 |
| **sqlmap**, a few endpoints | Confirms the ORM claim instead of assuming it | Staging, M7 |
| **radamsa** + a media corpus | §6.4's fuzz run | Staging worker, M7, timeboxed |
| **k6** or **vegeta** | §6.10's measurement — not load testing | Staging, M7 |

---

## 8. Severity, and what blocks the release

| Severity | Means | Response |
|---|---|---|
| **Critical** | Cross-account data access · remote code execution on any host · ledger manipulation · payment bypass · full account takeover | **Blocks launch.** Fix, regression test, retest, no exceptions |
| **High** | Authentication weakness, privilege escalation within an account, secret exposure, unauthenticated write | **Blocks launch**, or a written time-boxed acceptance signed by the project lead |
| **Medium** | Needs an unlikely precondition, or the damage is bounded | Ticketed with a date. Does not block |
| **Low / informational** | Hardening, defence in depth, best practice | Backlog |

**Launch is blocked while any critical or high is open.** That sentence is the
reason M7 is a milestone rather than a list of good intentions, and it is the
one thing in this document that should not be negotiated after a finding
appears.

Every fix ships with a test that fails without it. A security fix without a
regression test is a fix that comes back three refactors later.

---

## 9. Deliverables

| | What | Where |
|---|---|---|
| **1** | **The findings register** — id, severity, surface, reproduction, fix, retest date, status. Private, not a public issue tracker | `security/findings.md` |
| **2** | The fixes, each with its regression test | The code |
| **3** | **The build note**, in the shape of [`06-m2-notes.md`](06-m2-notes.md): what was decided, what was found, what the next phase inherits | `docs/08-m7-notes.md` |
| **4** | The CI gates from §7, running and green | `.github/workflows/` |
| **5** | A one-page summary for the project lead: what was tested, what was found, what is accepted and by whom | With the register |

---

## 10. What stays behind after M7

M7 ends. These do not.

- **The CI jobs from §7** run on every pull request, permanently.
- **The dependency patch policy** — a stated number of days, not a feeling.
- **`security.txt` and a disclosure address**, with a named person who answers it. Someone will find something after launch; the question is whether they can tell us.
- **The findings register keeps being used** for anything found later.
- **Every new tool that touches user media gets a short review of its own.** It is a new untrusted-input boundary, and §6.4 is the reason that matters.
- **The whole pass is repeated before phase 2 ships.** Phase 2 introduces facial data, which changes the risk picture more than any other planned change to this product.

---

## 11. Deliberately not in M7

| Not covered | Why, and when |
|---|---|
| Biometric data, consent flow, retention and deletion for face profiles | None of it exists in phase 1 ([`02-scope-v1.md`](02-scope-v1.md) §5). It is the first item of the phase 2 review, and it needs a named owner before that phase starts |
| GDPR / DPA / privacy-policy work, data-processing agreements | Not a development task. Needs the same owner as tax and retention — see [`README.md`](README.md#status) |
| SOC 2, ISO 27001, any certification | Not a startup-at-launch concern. An internal review is not an audit and must never be described as one |
| An external penetration test by a firm | **Recommended, not scheduled.** Worth budgeting once there is revenue: an outside team is the only thing that corrects for §1's known weakness |
| A bug bounty programme | After the disclosure address has existed quietly for a while, not at launch |
| DDoS resilience beyond rate limits and CloudFront | Infrastructure work with a cost attached; a decision for after the first traffic numbers exist |
| Physical security, staff devices, social engineering | §2 |

---

## 12. Decisions recorded here

| # | Decision | Instead of | Because |
|---|---|---|---|
| 1 | Security is one dedicated milestone at the end, plus standing CI gates | A checklist item inside every milestone | The attacks that matter cross components, and none exist until the system does (§1) |
| 2 | Testing happens on staging with synthetic data only | Testing production, which is where the real configuration is | Our worst-case asset is unpublished user footage. Staging deployed from the release commit is close enough (§2) |
| 3 | Providers are never targets; their sandboxes are | End-to-end testing against live Stripe and Razorpay | What we need to prove is our verification code, and the other version is a contract violation (§2) |
| 4 | Critical and high findings block the launch | A risk register everything gets added to | A gate that bends under the first finding was never a gate (§8) |
| 5 | The ingest worker is treated as the primary target | Spreading the effort evenly across the API surface | It is the only place an attacker's bytes reach a C parser unattended (§6.4) |
| 6 | Fuzzing is timeboxed and triaged for crashes only | A full fuzzing campaign, or none at all | We want a decision about upgrading or sandboxing FFmpeg, not an exploit (§6.4) |
| 7 | An external test is recommended for after launch, not before | Claiming an internal review is equivalent | It is not equivalent, and the honest version is the one that gets funded later (§1, §11) |

---

*Part of the documentation set · 17 August 2026 · maintained by MMaxouB*
