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
| **Docker** + Compose | Postgres, Redis, MinIO, the API | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) |
| **Python 3.12+** | Backend | System package |
| **Node 20+** with corepack | Frontend | System package, then `corepack enable` |
| **FFmpeg** | Media ingest and export (needed from M2) | `sudo apt install ffmpeg` |

### First run

```bash
make setup      # copies .env, installs both sides
make up         # starts Postgres, Redis, MinIO, API, worker, beat
make migrate    # applies database migrations
```

`make up` needs your user to be in the `docker` group — `sudo usermod -aG docker $(id -un)`, then **log out and back in**. Group membership is fixed when a session opens; it never changes mid-session.

### Without Docker

Pulling images from Docker Hub is slow on some connections. Postgres and Redis from apt behave identically for everything up to M2 — only MinIO (object storage, first needed when uploads land) has no native equivalent.

```bash
sudo apt install postgresql redis-server
sudo systemctl start postgresql@18-main redis-server
sudo -u postgres psql -c "CREATE ROLE zipzop LOGIN PASSWORD 'zipzop' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE zipzop OWNER zipzop;"

make native-check   # confirms all three are reachable
make migrate
make dev            # API, natively, with reload
```

Adjust the port in `.env` if your cluster is not on 5432 — `pg_lsclusters` shows what you have.

Then in a second terminal:

```bash
make dev-frontend
```

| | |
|---|---|
| Frontend | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |
| MinIO console | http://localhost:9001 — `zipzop` / `zipzop-dev-secret` |

`make help` lists every target.

**No AWS account is required to develop.** MinIO speaks the same presigned-URL protocol as S3, so uploads, ingest and export are built and tested entirely locally.

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
