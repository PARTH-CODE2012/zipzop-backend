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
make infra      # Postgres, Redis, MinIO — infrastructure only, nothing to build
make migrate    # create the schema and seed the four plans
make dev-all    # the API, the ingest worker and the dev server
```

Then open **http://localhost:3000/editor/scratch**, create an account, and drop a video in.

Anything unexpected:

```bash
make doctor     # says exactly what is missing, and what to run for it
```

### What each service is for

| | | Without it |
|---|---|---|
| **Postgres** | accounts, media rows, the credit ledger | nothing starts |
| **Redis** | Celery's broker, rate limits, idempotency keys | nothing starts |
| **MinIO** | object storage — the file itself | uploads fail; it is not optional any more |
| **API** | `:8000` | the frontend shows a sign-in form that cannot submit |
| **ingest worker** | ffprobe, proxy, thumbnail, waveform | **an upload reaches "Preparing…" and stays there for ever** |
| **frontend** | `:3000` | — |

The worker is the one people forget. Nothing errors when it is absent, because
nothing failed: the job sits in the queue with no one to take it, and the media
bin spins with no message to read.

| | |
|---|---|
| Editor | http://localhost:3000/editor/scratch |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |
| MinIO console | http://localhost:9001 — `zipzop` / `zipzop-dev-secret` |
| Logs | `/tmp/zipzop-{api,worker,web}.log` |

`make dev-stop` stops the three application processes; `make help` lists every target.

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
