# ZipZop — AI Video Editor

A web video editor with a real timeline, where AI tools sit in the toolbar next to the ordinary ones. Trim the silences, generate the captions, grade the picture — the results land on the timeline, where they can be adjusted or undone like any other edit.

> **Documentation lives in [`docs/`](docs/).** Start with [`docs/README.md`](docs/README.md), then [`docs/02-scope-v1.md`](docs/02-scope-v1.md) for what is being built right now.
>
> **Day-to-day task list:** [`PHASE1-TASKS.md`](PHASE1-TASKS.md)

---

## Layout

This is a monorepo. The repository is named `zipzop-backend` for historical reasons; it holds both sides.

```
backend/        FastAPI · PostgreSQL · Redis · Celery · FFmpeg
frontend/       Next.js · TypeScript · WebGL2
docs/           architecture and product documentation
openapi.json    the API contract — generated, and committed on purpose
```

**Why one repository.** The frontend generates its types from `openapi.json`. Keeping both sides together means a change to the contract and its two consumers lands in one commit, and a mismatch is a build failure rather than a bug found at integration. Splitting later is straightforward if the team grows.

---

## Getting started

### Prerequisites

| | Needed for | Install |
|---|---|---|
| **Python 3.12+** | Backend | System package |
| **Node 20+** with corepack | Frontend | System package, then `corepack enable` |
| **FFmpeg** | The ingest worker shells out to it. **Every upload fails without it** | `sudo apt install ffmpeg` |
| **Docker** + Compose | Postgres, Redis and MinIO | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) |

### First run — four commands

```bash
make setup      # .env, both dependency trees, and the generated API types
```

```bash
make watch      # infrastructure, schema, then the API + worker + dev server
```

`make watch` is the whole thing: it checks the containers, migrates, resolves the
ports and opens one tmux session with the three servers visible. Then open
**http://localhost:3123/editor/scratch**, create an account, and drop a video in.

> **Why 3123 and not 3000.** 3000 and 8000 are the two most contested ports on a
> developer's machine — every Next app and every Python service wants them. When
> another project has one, this stack used to fail in three different ways
> depending on which half won, and the worst of them was silent: the editor
> talked to *the other project's* API, got a 404 from `/health`, and reported
> that it could not reach the server. `scripts/ports.sh` now resolves both ports
> before anything binds, steps over whatever is already there, and tells the
> frontend where the API actually landed.
>
> ```bash
> make ports
> ```
>
> prints what the next `make watch` will use. Override with `API_PORT` and
> `WEB_PORT` in `.env`.

Anything unexpected:

```bash
make doctor     # says exactly what is missing, and what to run for it
```

### Running it in your own terminals

`make dev-all` backgrounds all three processes and buries their output in
`/tmp`, which is the wrong tool the moment something misbehaves — a traceback
you need is somewhere you are not looking. Two better options, in order of
convenience:

**`make watch`** — one command, one tmux session, three windows (`api`,
`worker`, `web`), each running exactly the foreground command below. Every
window's output is live on screen and `Ctrl-C` inside one stops only that
service. It checks and starts infrastructure first, so it is genuinely the
one command to run.

```bash
make watch
```

```
Ctrl-b then 0 / 1 / 2     switch window
Ctrl-b then d             detach — leaves everything running
tmux attach -t zipzop     reattach later
make watch-stop           stop all three
```

No tmux, or you would rather see three separate terminal windows: type the
three commands below yourself, one per terminal.

**Once, and they stay up on their own** — detached containers, so closing the
terminal does not stop them:

```bash
make infra
```

```bash
make migrate
```

`make migrate` is the one people skip, and it is the one that bites. It runs
against the **development** database; the test suite migrates `zipzop_test` by
itself, so a green `make test` says nothing about whether the app can start.
Skipping it means the API answers `500` on the very first query — creating an
account — with nothing on screen but *"something went wrong"*.

**Then one terminal each**, in the foreground, stopped with `Ctrl-C`:

```bash
make dev
```

or, spelled out — `make ports` says which number to use, and `make dev` reads it
for you:

```bash
cd backend && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8123 --reload
```

```bash
cd backend && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing --concurrency=2
```

```bash
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:8123/v1 pnpm dev --port 3123
```

**The environment variable is not optional when you type this by hand.** Without
it the browser bundle falls back to the default in `.env.example`, and if your
API is on a different port the editor will quietly talk to nothing — or worse, to
whatever else is listening. `make dev-frontend` sets it from the resolved port.

Then **http://localhost:3123/editor/scratch**. A route segment that is not a
real project id means *open a fresh project*: the app creates one and swaps its
id into the address bar, so reloading returns to the same project rather than
making another.

To check the API is really up before blaming the interface:

```bash
curl -s http://localhost:8123/health
```

`"status": "ok"` with both dependencies `true` is the only answer that means the
stack is ready. `"degraded"` names which one is down.

When you are done — the servers stop with `Ctrl-C`, the containers with:

```bash
make down
```

> **These commands belong in a terminal you control.** Anything long-running —
> the API, the worker, the dev server — should be started by the person at the
> keyboard, not from an assistant's tool session: those processes live in a
> shell you cannot see, cannot stop, and whose logs you cannot read, and they
> can die between one command and the next while everything still looks fine
> from the outside. Short commands are fine anywhere. Detached containers are
> the exception, because `docker` can manage them from any shell.

### What each service is for

Looking at what is actually in the database — accounts, projects, the credit
ledger — is one command:

```bash
make psql
```

```sql
\dt                    -- every table
SELECT id, email, display_name, status FROM users;
```

`\q` to leave. Any ordinary Postgres client (DBeaver, TablePlus, the VS Code
PostgreSQL extension) connects the same way: `localhost:5432`, database
`zipzop`, user and password `zipzop` — the dev defaults in `backend/app/config.py`.

| | | Without it |
|---|---|---|
| **Postgres** | accounts, media rows, the credit ledger | nothing starts |
| **Redis** | Celery's broker, rate limits, idempotency keys | nothing starts |
| **MinIO** | object storage — the file itself | uploads fail; it is not optional any more |
| **API** | `:8123` | the frontend shows a sign-in form that cannot submit |
| **ingest worker** | ffprobe, proxy, thumbnail, waveform | **an upload reaches "Preparing…" and stays there for ever** |
| **frontend** | `:3123` | — |

The worker is the one people forget. Nothing errors when it is absent, because
nothing failed: the job sits in the queue with no one to take it, and the media
bin spins with no message to read.

| | |
|---|---|
| Editor | http://localhost:3123/editor/scratch |
| API docs | http://localhost:8123/docs |
| Health | http://localhost:8123/health |
| MinIO console | http://localhost:9001 — `zipzop` / `zipzop-dev-secret` |
| Logs | `/tmp/zipzop-{api,worker,web}.log` |

`make dev-stop` stops the three application processes — **this project's only**,
filtered on their working directory, because a `pkill -f "next dev"` also kills
every other checkout's dev server. `make help` lists every target.

**No AWS account is required to develop.** MinIO speaks the same presigned-URL protocol as S3, so uploads and ingest are built and tested entirely locally.

### Two things that will cost you an afternoon otherwise

**Run `pnpm` from `frontend/`, never from the repository root.** There is no
`packageManager` field at the root, so corepack resolves whatever it last
downloaded — on Node 24 that is pnpm 11, which crashes with
`ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING` before printing anything. `frontend/`
pins pnpm 9 and works. Every `make` target already does this for you.

**`frontend/src/lib/api/generated.ts` is gitignored and generated.** It comes
from the committed `openapi.json`; committing it too would let the two drift.
A fresh clone therefore has no API types, and `pnpm typecheck`, `pnpm build`
and `pnpm dev` all fail on a missing module until `make types` has run.
`make setup` does it — but if you installed by hand, that is why.

### When the editor will not start

| What you see | What it is | Fix |
|---|---|---|
| `500` on sign-up, or *"something went wrong on our side"* | The development database has no schema — `alembic` has only ever run against `zipzop_test` | `make migrate` |
| *"Cannot reach the server…"* on the sign-in panel | The request never left the browser: the API is not running, or is on another port | `make doctor` — it names the process holding the port if it is not ours |
| `{"detail":"Not Found"}` from `/health` | Something that is **not** this API is on that port. Another project, almost always | `make doctor`, then stop it or set `API_PORT` in `.env` |
| `[Errno 98] address already in use` | The port was taken when the API tried to bind | `make watch` steps over it by itself; `make ports` shows what it picked |
| `/health` says `degraded` | Postgres, Redis or MinIO is down. Containers do not restart themselves after a daemon restart | `make infra` |
| The editor `500`s **after** a `pnpm build` | The build overwrote the `.next/` the running dev server was using | `Ctrl-C`, `rm -rf frontend/.next`, start `pnpm dev` again |
| Uploads fail, everything else works | FFmpeg is missing, or MinIO is down | `ffmpeg -version`, then `make infra` |

### Without Docker

Postgres and Redis from apt behave identically. **MinIO has no apt equivalent
and is now required**, so this path needs Docker for that one container.

```bash
sudo apt install postgresql redis-server
sudo systemctl start postgresql@18-main redis-server
sudo -u postgres psql -c "CREATE ROLE zipzop LOGIN PASSWORD 'zipzop' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE zipzop OWNER zipzop;"

make native-check   # confirms Postgres and Redis are reachable
```

Adjust the port in `.env` if your cluster is not on 5432 — `pg_lsclusters` shows what you have.

If `docker` says *permission denied*, your session has not picked up the group.
`sudo usermod -aG docker $(id -un)` and log out and back in — or, without
logging out, prefix the command: `sg docker -c "docker compose up -d minio"`.

### Seeing it work end to end

```bash
make e2e        # 29 checks, driving a real Chromium from an empty database to a clip playing back
```

[`frontend/e2e/README.md`](frontend/e2e/README.md) covers what it checks and the
three defects the browser found that a green unit suite could not.

---

## Working on this

### The contract comes first

`openapi.json` is generated from FastAPI and **committed**. The frontend generates its TypeScript types from it. After changing any endpoint or schema:

```bash
make types      # regenerates openapi.json and the frontend types
```

CI fails if `openapi.json` is stale. That is deliberate — it is the one file that keeps both sides honest.

### Before pushing

```bash
make check      # lint, type-check, tests, contract freshness
```

---

## Things that will bite you if nobody said them

- **Times are integer milliseconds**, everywhere, on both sides. Never seconds, never floats.
- **Money is integer minor units** — cents, paise — with its currency beside it. Never floats.
- **Spatial values are normalised 0–1** relative to the canvas, never pixels. This is what makes a 480p preview and a 1080p export agree.
- **The editor is client-only.** Next.js earns its place on the marketing and pricing pages; the editor itself is `'use client'` throughout, because WebGL, video elements and timeline state cannot be server-rendered.
- **Plan limits are enforced server-side.** Client-side gating exists so a button can be greyed out, never as the only check.
- **Original media is never modified.** Any tool that changes pixels writes a *new* asset recording what it came from.

The reasoning behind each of these is in [`docs/`](docs/).

---

## Status

Phase 1 in progress. Nothing blocking — the product position, phase 1 scope, commercial model, cloud and payment providers are all agreed. See [`docs/README.md`](docs/README.md#status).
