# M7 readiness — the attack surface as it was actually built

**Written 20 September 2026, from a read of the code rather than of the plan —
for whoever runs M7, which is very likely a later version of me.**

[`07-security.md`](07-security.md) is the plan: scope, rules of engagement,
threat model, severity gate. It stands, and **nothing here replaces it**. But it
was written on **17–18 August**, which is before M4, M5 and M6 existed. It
anticipates their shape from the architecture document; it has never seen their
code.

This note is the difference. It is the map of what is actually there now: every
place user input reaches something dangerous, what reading has already cleared,
and what is left to prove by attacking it.

> ⚠️ **Nothing here has been exploited.** Every line below is either *confirmed
> by reading the source* or *a hypothesis to test*, and each is labelled. Do not
> carry a hypothesis into a findings register as a finding — §8 of
> `07-security.md` sets the severity gate, and it works on evidence.

| | |
|---|---|
| **Milestone** | M7 — the security review, and the last before launch |
| **Plan** | [`07-security.md`](07-security.md) — read §2 and §8 before anything |
| **Status** | 🔴 **Blocked on two things that are not code** — §1 |
| **Code reviewed for this note** | 33 routes · 4 subprocess call sites · 25 unscoped queries |

---

## 1. 🔴 Two blockers, and they are not code

**There is no staging environment, and nothing is deployed anywhere.** No
`Dockerfile` for the frontend, no deploy workflow, no host chosen. `07-security.md`
§2 is unambiguous: the test runs **on staging, deployed from the release commit,
with synthetic data, never production**. Right now there is no "deployed"
anything — the stack runs on one developer's laptop.

Most of Part A (the code review) can be done without it. **Almost none of Part B
can.** Presigned-URL abuse, IMDS access from the worker, rate limits behind a
load balancer, and browser-origin questions all need a real deployment to mean
anything. Testing them against `localhost` measures the laptop.

**The rules of engagement are not signed.** §2 wants scope and dates agreed in
writing with the project lead, including who can call a stop. That is a message,
not a task, and it should go out before the first probe — not because anyone
will object, but because "I was told to" is the only thing separating this from
the thing it imitates.

---

## 2. 🔴 What reading already found

Ranked by how likely each is to be real and how much it would cost. **All of
these need proving.** Several are exactly what `07-security.md` predicted, which
is a good sign for that document and a bad one for the code.

### 2.1 FFmpeg has no protocol allowlist — the sharpest edge in the product

**Confirmed by reading. Untested.**

`grep -rn "protocol_whitelist" backend/app` returns **nothing**. `ffprobe` and
`ffmpeg` are invoked on user-uploaded files with no restriction on which
protocols the demuxer may follow:

```python
# app/services/ingest.py:96
["ffprobe", "-v", "error", "-print_format", "json",
 "-show_format", "-show_streams", str(path)]
```

FFmpeg will follow references *out of a container*. An HLS playlist, a `concat`
script, or a container with an external reference can name `http://…`,
`file:///…` or `data:`. That is the whole of `07-security.md` §6.4, and there is
currently nothing in the way of it.

**What to test, in this order:**

| Probe | What it would mean |
|---|---|
| `.m3u8` referencing `http://169.254.169.254/latest/meta-data/iam/security-credentials/` | **Critical.** AWS credentials from the instance role. Needs a deployed worker on EC2 to be real; on a laptop it proves the fetch, not the theft |
| `file:///etc/passwd` as a segment reference, then read the output or the error | Local file read into a render the attacker downloads |
| A container referencing an internal service (`http://redis:6379/`) | Lateral movement inside the compose network |
| Decode bomb: tiny file, enormous declared dimensions | Memory exhaustion. There is a 60 s timeout on probe, none obvious on memory |

**The fix, if confirmed, is one argument** — `-protocol_whitelist file` on every
`ffprobe`/`ffmpeg` invocation that touches user media, widened only where a
format genuinely needs more. It belongs in the same shared module the path
escaping went into (`services/ffmpeg_filters.py`), for the same reason: a second
private copy is how the first one's bug comes back.

⚠️ **Also check egress.** Even with a whitelist, the worker container should not
be able to reach the internet or the metadata endpoint at all. `docker-compose.yml`
sets no network restriction on the worker.

### 2.2 M6 added four tables and no scoped repository

**Confirmed by reading. The risk is structural, not a specific hole.**

`ScopedRepository` (`repositories/base.py:66`) exists precisely so a user filter
cannot be forgotten: `_select()` is the only entry point and the filter is
already on it. Three repositories use it — `Job`, `MediaAsset`, `Project`.

**M6 added none.** `Template`, `PromoCode`, `CommissionLedgerEntry`,
`ProviderPlan`, `Payment` and `Subscription` are queried directly, with the user
filter written by hand each time. **25 such queries** across
`api/routes/billing.py`, `api/routes/templates.py`, `services/billing/service.py`
and `services/promo.py`.

Spot-reading says the hand-written filters are currently correct — for example
`templates.py:152` fetches by id and then compares `row.user_id != user.id`, and
`billing.py:450` does the same for a promo code's owner. But *currently correct*
is the wrong property. The pattern the codebase chose makes the filter
impossible to forget; these do not.

**Test every one of them**, and treat this as the isolation work in
`07-security.md` §6.2 rather than as a separate item:

- `GET /credits/ledger` — another user's rows in the page?
- `GET /promo/{code}/stats` — a code owned by somebody else (should 404, not 403)
- `DELETE /templates/{id}` — somebody else's template
- `POST /billing/cancel` — can it reach another subscription?
- The ledger cursor — is `?cursor=` a row id that can be pointed anywhere?

### 2.3 `GET /v1/catalog/luts` has no rate limit at all

**Confirmed by reading.**

```python
# app/api/routes/catalog.py:23
router = APIRouter(prefix="/catalog", tags=["catalog"])   # no dependencies=
```

Every other public route carries `Depends(general_rate_limit)`. This one does
not, and it hits the filesystem on each call — `luts.available()` globs a
directory, deliberately uncached. Low severity, trivially fixed, and the kind of
thing that is embarrassing to have pointed out by somebody else.

### 2.4 No Content-Security-Policy

**Confirmed by reading.** `frontend/next.config.ts` sets three headers —
`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` — and **no CSP**.
`07-security.md` §5.2 asks for one, plus `frame-ancestors`.

This matters more than it usually would because of the refresh-token design:
contract 1.2 moved the refresh token into an httpOnly cookie on the argument
that *an XSS which can call the API still cannot walk away with a 30-day
credential*. That argument is worth testing rather than assuming (§6.9 of the
plan asks exactly this), and a CSP is the control that makes it true in practice.

**Good news from the same read:** no `dangerouslySetInnerHTML` and no
`innerHTML =` anywhere in `frontend/src`. The obvious injection route is closed.

### 2.5 The watermark text is a sink with no guard on it

**Confirmed by reading. Not currently reachable.**

```python
# app/services/render_graph.py:293,364
watermark_text: str = "ZipZop",
...
f"text='{watermark_text}':"
```

Interpolated into a `drawtext` filter with **no escaping**. Today only the
default ever reaches it, so there is no vulnerability. But `drawtext` expands
`%{…}` sequences, a `'` ends the option, and a `:` ends the field — so the day
anybody wires a plan name, a display name or a custom watermark into that
parameter, it is an injection into the filter graph.

Either escape it now or make it a module constant so it cannot take an argument.
The second is honest about what it is.

### 2.6 The WebSocket token travels in the query string

**Confirmed by reading, and deliberate.** `ws.py:39` takes
`token: str = Query(default="")`, with a correct reason in the docstring:
browsers cannot set headers on a WebSocket handshake.

The token is short-lived (15 minutes) and the channel is derived from it, so
cross-user subscription looks closed. What is *not* closed is where that URL
ends up: access logs, proxy logs, and anything that records a request line.
**Check the deployed load balancer's log format** — this is a log-hygiene
finding, not an auth one, and it belongs with §5.1's presigned-URL rule.

---

## 3. What reading has already cleared

Recording these so M7 does not spend a day re-deriving them. Each was checked by
grep or by reading the function, and each is a thing `07-security.md` asks about.

| Question | What the code does |
|---|---|
| **Shell injection** | No `shell=True` anywhere. Every subprocess call passes an argument list |
| **SQL injection** | No f-string SQL, no `text()` with interpolation. One `text()` in `models/project.py:52`, a static server default |
| **HTML injection** | No `dangerouslySetInnerHTML`, no `innerHTML =` in the whole frontend |
| **LUT path traversal** | `luts.path_for` validates the name against the directory listing rather than interpolating it, and production passes that resolver (`render_pipeline.py:123`). `../` cannot resolve |
| **Filename → S3 key** | `_extension` (`media.py:96`) takes the basename, then requires 1–5 **alphanumeric** characters. `original_filename` stores `PurePosixPath(...).name`. No traversal into a key |
| **Timeline numeric bounds** | Better than the plan assumed. Colours are `^#[0-9A-Fa-f]{6}$`; `font_size`, `stroke_width`, `position.x/y`, `crop.*` are 0–1; `speed` 0.25–4; `transform.scale` 0–10; text capped at 2 000 characters. NaN and Infinity fail the `ge`/`le` comparisons |
| **ASS override injection** | `render_text.py:80` escapes `\`, `{`, `}` and every newline form before user text reaches the subtitle file |
| **Two-level filter escaping** | `ffmpeg_filters.escape_path` — one shared implementation, and the history of why is in its docstring |
| **Container hardening** | `backend/Dockerfile:47` creates a uid-1000 user and `USER app` in the prod stage. No Docker socket, no `privileged`, no `cap_add` in compose |
| **Secrets in logs** | No log call carries a URL, token, secret or password |

**Gaps inside that good news, worth one test each:**

- `duration_ms` on `MediaClip` and `TextClip` has **no upper bound** (deliberate
  for media — invariant 3 owns it). What does `_timestamp()` produce for 10¹⁸ ms?
- `Transition.duration_ms` is `ge=0` with **no ceiling**. Invariant 7 clamps it
  client-side; prove the server does too.
- `ColorGradeEffect.lut` is `max_length=64` with **no pattern** — safe only
  because `path_for` is an allowlist. If that resolver is ever swapped, the bound
  moves.
- Does FastAPI accept the JSON literals `NaN` and `Infinity`? Python's `json`
  does by default. If they survive parsing, the `ge`/`le` bounds catch them —
  but confirm rather than assume.

---

## 4. The M6 surface, which the plan never saw

`07-security.md` §6.7 covers webhooks in four lines written before one existed.
Here is what is actually there.

**Two unauthenticated routes**, `/v1/webhooks/razorpay` and `/v1/webhooks/stripe`.
The signature is the entire defence. What was verified in September with the real
dashboard key: a correctly signed body is accepted, one byte changed is refused,
a forged signature is refused, an unconfigured verifier **fails closed**, a
redelivery is dropped. **What has never happened is a delivery from Razorpay
itself** — the endpoint points at a placeholder URL. So "we verify the way
Razorpay signs" is still an assumption, and M7 is the right moment to settle it.

**Test beyond the signature:**

- An event whose `notes.user_id` names **another user's id**. The signature is
  valid — we signed it — so this asks whether a compromised or careless dashboard
  operator could move a plan onto someone else's account. `_user_for_event`
  (`service.py:597`) trusts `notes.user_id` first, then falls back to the pending
  `payments` row.
- The same event id replayed after `processed_at` is set.
- Events out of order: `charged` for period 2 before period 1.
- A `plan` in the notes that does not exist (should log and drop — verify).
- An oversized body. Is there a request size limit on this route?
- A `payment_link.paid` with a `pack` naming a pack that does not exist, and one
  naming a pack **larger than what was paid for**.

**The commission ledger is a money path with an incentive attached.** A Discord
server owner benefits from inflating it. Test: self-referral (use your own code),
a code applied to an account that then charges back, and whether
`GET /promo/{code}/stats` leaks anything about *who* signed up rather than how
many. `07-security.md` predates promo codes entirely.

**`templates.settings` is stored JSON the server never interprets.** Capped at
64 KB. It is rendered by the editor. Test what happens when it contains a
`__proto__` key, a deeply nested object, or a string where the editor expects a
number — the server will store all three happily.

---

## 5. Order to work in

The plan's §5 and §6 are the checklist. This is how to sequence it given where
the project actually is.

1. **Send the rules-of-engagement message** (§1). One paragraph, and it unblocks
   everything that follows.
2. **Turn on the automated gates first** — `semgrep`, `bandit`, `gitleaks`,
   `pip-audit`, `pnpm audit`, `trivy`. `07-security.md` §7 lists them and the
   task list says they are cheap early and expensive late. There is currently
   **one** CI workflow (`.github/workflows/ci.yml`) and none of these are in it.
   Do this before reading anything by hand: it is free, and it changes what is
   worth reading.
3. **Part A, the code review**, which needs no deployment. §2.2's twenty-five
   queries are the bulk of it.
4. **Fix §2.1 and §2.3 immediately** — a protocol allowlist and a rate limit are
   both one line, and neither needs a test campaign to justify.
5. **Get a staging deployment.** Everything left in Part B needs it, and it is
   the same decision blocking the webhook URL and the "test link" the project
   lead has been asking for. One decision unblocks three things.
6. **Part B**, starting with §6.2 (isolation) and §6.4 (the ingest worker),
   because those are the two with the largest blast radius.
7. **Fix, retest from the register, write `docs/08-m7-notes.md`.**

---

## 6. What to produce

From `07-security.md` §9, unchanged, and worth repeating because the register is
the part people skip:

- **`security/findings.md`** — private, never a public issue. One entry per
  finding, with severity, evidence, and the commit that closes it.
- **A regression test per fix**, failing without it. The M6 notes are full of
  fixes that were only real once a test bit; §2.2 of
  [`21-m6-notes.md`](21-m6-notes.md) is the cautionary one.
- **`docs/08-m7-notes.md`**, in the shape of [`06-m2-notes.md`](06-m2-notes.md).
- **`security.txt`**, with an address and a named person who answers it.
- **Any accepted risk written down, time-boxed, and signed by the project lead.**

---

## 7. The honest framing

`07-security.md` §1 says it and it is worth keeping in front of you: **this is a
review of one's own code**, and that is its central weakness. The person who
wrote the escaping is the person deciding whether the escaping is enough.

Two things make it less bad. The automated gates do not care who wrote the code.
And the findings in §2 above came from reading with the specific intent of
breaking it — which is why four of them are things the author knew and had
filed as "fine for now".

An external test is still the only real correction, and §11 recommends it once
there is revenue. Say so in the notes rather than implying the internal one is
equivalent.

---

*Readiness note · 20 September 2026 · written before M7, from the code rather than from the plan*
