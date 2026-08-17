# Backend

FastAPI, SQLAlchemy 2.0, Alembic, Celery. Architecture in
[`../docs/03-backend-architecture.md`](../docs/03-backend-architecture.md); the
interface it serves is [`../docs/05-api-contract.md`](../docs/05-api-contract.md).

```bash
make install-backend     # venv + dependencies
make migrate             # apply migrations
make dev                 # the API, with reload
make dev-worker          # a Celery worker
make test-backend        # needs Postgres, Redis, MinIO and ffmpeg
```

---

## Layout

```
app/
  api/
    routes/          one module per resource — auth, media, health
    schemas/         pydantic request and response shapes (camelCase on the wire)
    deps.py          who is calling, and may they call this often
    errors.py        one envelope, one class per code from contract §9
    ids.py           usr_ / ast_ / prj_ prefixes on public identifiers
  models/            SQLAlchemy tables, one module per group, enums.py for the types
  repositories/      scoped queries — see "Nothing leaks between accounts"
  services/          security, storage, ingest, rate limiting, plans
  workers/tasks/     Celery tasks: a loop, a session, and retries. No logic
alembic/versions/    the migration chain
tests/               against real infrastructure, never mocks
```

The split that matters is **services versus workers**. Everything ffmpeg does
lives in `services/ingest.py` as plain functions over local paths — no database,
no S3, no Celery — so each step is tested against real media without standing
anything up. `workers/tasks/ingest.py` owns only the queue's concerns.

---

## Nothing leaks between accounts

PHASE1-TASKS.md puts it plainly: *"a route that forgets is a data leak."*

The defence is structural rather than a review habit. A `ScopedRepository`
cannot be constructed without a user, and every query it issues starts from
`_select()`, which has the filter already applied. There is no method that
hands back an unfiltered statement, so forgetting is not something a caller can
do by omission.

`tests/test_media.py::test_no_endpoint_returns_another_accounts_media` proves
the property from the outside: one asset, two accounts, every media endpoint.
The stranger gets `404` rather than `403` — telling them apart would confirm
the id exists.

The identity layer (`repositories/user.py`) is deliberately *not* scoped. It is
the one place that legitimately looks a row up by something other than the
caller's own id, because at that point there is no caller yet.

---

## Auth

Three mechanisms, on purpose:

| | | |
|---|---|---|
| **Passwords** | bcrypt cost 12 | SHA-256'd first, so a passphrase over 72 bytes is neither truncated nor rejected — the same construction as passlib's `bcrypt_sha256` |
| **Access tokens** | signed JWT, 15 minutes | Stateless, so no database read per request; short, so a leaked one stops working |
| **Refresh tokens** | opaque, 30 days, SHA-256 in the database | Opaque because they must be **revocable**, and a signed token that cannot be withdrawn is the last thing you want holding a month-long session open |

**The refresh token is an httpOnly cookie**, not a body field — contract §2,
version 1.2. No script can read it, so an XSS that can call the API as the user
still cannot walk away with a 30-day credential. The cost is that a non-browser
client cannot hold a session; phase 1 is web only, and the mobile app in phase 3
needs a second grant type rather than a change to this one.

Rotation is enforced: each refresh issues a new token and marks the old one
replaced. **Presenting an already-rotated token revokes the whole chain.**
Either the legitimate client lost a response and retried, or a copy leaked, and
those are indistinguishable from the server — a false positive costs one login,
a false negative costs the account.

Login answers identically for a wrong password and an unknown email: same
status, same code, same message, and a dummy verify against a fixed hash so the
two take the same time. A timing difference enumerates accounts just as clearly
as a different error would.

---

## Media and ingest

Files never pass through this process. The browser gets a presigned URL and
uploads straight to storage, because bandwidth through a request handler is
wasted money and a needless failure mode.

1. `POST /media/uploads` — check the quota, reserve a row, return a presigned
   PUT good for 15 minutes.
2. `PUT <uploadUrl>` — browser to S3. **No `Authorization` header**; adding one
   breaks the signature, and the failure reads like bad credentials.
3. `POST /media/{id}/complete` — verify the object is really there and the right
   size, then enqueue ingest. This step exists because step 2 happens somewhere
   this service cannot see: a client saying it finished is not evidence.

Ingest produces four things, and the asset is `ready` only when all of them
exist — kind-aware, so an audio upload is not held forever waiting for a
thumbnail it can never have:

| Probe | `ffprobe` → duration, **display** dimensions, fps, codecs |
| Proxy | 480p H.264 faststart, and it never upscales |
| Thumbnail | JPEG from ~10% in, falling back to frame zero on files too short to seek |
| Peaks | one amplitude per bucket at 100 buckets/second |

Two details worth keeping:

**Display dimensions, not stored ones.** A phone recording portrait stores a
landscape frame plus a rotation flag. ffmpeg applies the rotation on decode, so
the proxy comes out portrait — reporting the stored dimensions would put every
such upload on the timeline with its aspect ratio on its side.

**Peaks are the peak, not the mean.** RMS flattens exactly the transients a
waveform is read for. The extractor is cross-checked against ffmpeg's own
`volumedetect` rather than against our own arithmetic, because an extractor
that is internally consistent and wrong by a constant factor produces a
waveform that looks entirely plausible and is not the audio.

**Everything in storage is private.** Reads go through a signed URL that expires
in an hour. There is deliberately no `public_url()` helper — a function that
builds an unsigned link is one import away from being handed to a client.

---

## Tests

They run against **real infrastructure**: a real Postgres, a real MinIO, real
ffmpeg. Nothing is mocked, because the things M2 gets wrong are exactly the
things a mock cannot reproduce — a presigned signature that does not verify, a
partial index that does not fire, an ffprobe field that is a string on one
container and a number on another.

```bash
make test-backend
```

The cost is that those services have to be up. The alternative costs more: a
green suite that proves the mock works.

- The test database is created and migrated by a fixture, and **guarded** — it
  refuses to run against any database whose name does not end in `_test`.
- Each test runs inside a transaction that is rolled back, so application code
  can `commit()` (and does) without escaping the rollback.
- `tests/test_schema.py` asserts what the *database* enforces: the plan seed
  values, the credit-ledger unique index, the check constraints, `ON DELETE
  RESTRICT` on an asset in use. If a migration is edited and one of them quietly
  disappears, no application test would notice — the application would simply
  start being able to do something the design forbids.

`alembic check` runs in CI. It is the guard against a model and a migration
drifting apart, and it earned its place immediately: on its first run it caught
check constraints taking a double `ck_` prefix, and `postgresql_ops` being used
to express sort order when it carries operator classes.
