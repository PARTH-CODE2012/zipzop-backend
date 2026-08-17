#!/usr/bin/env bash
# Tell someone with a fresh clone exactly what is missing, in one run.
#
# Every check here corresponds to something that, when absent, fails with a
# message that does not name the real cause: a missing generated.ts reads as a
# broken import, a missing worker reads as an upload that never finishes, a
# missing ffmpeg reads as an ingest that fails on every file.
#
# Never exits non-zero for a missing *optional* piece — the point is to inform,
# not to block.

set -uo pipefail
cd "$(dirname "$0")/.."

green() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
warn()  { printf '  \033[33m--\033[0m   %s\n' "$1"; }
bad()   { printf '  \033[31mMISS\033[0m %s\n' "$1"; MISSING=1; }
MISSING=0

echo
echo "Tools"
command -v python3 >/dev/null && green "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || bad "python3 — needed for the backend"
command -v node    >/dev/null && green "node $(node -v)"                            || bad "node — needed for the frontend"
# pnpm is checked *from frontend/*, never from the repository root. There is no
# packageManager field at the root, so corepack resolves whatever it last
# downloaded — on Node 24 that is pnpm 11, which crashes with
# ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING before it prints anything. frontend/
# pins pnpm 9 and works. Every pnpm command in this project runs from there.
if ! command -v pnpm >/dev/null; then
  bad "pnpm — run: corepack enable"
elif PNPM_VERSION=$(cd frontend && pnpm -v 2>/dev/null) && [ -n "$PNPM_VERSION" ]; then
  green "pnpm $PNPM_VERSION (from frontend/ — the root crashes on Node 24, see below)"
else
  bad "pnpm cannot run even from frontend/ — try: cd frontend && corepack install"
fi
command -v ffmpeg  >/dev/null && green "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)" \
    || bad "ffmpeg — the ingest worker shells out to it; every upload will fail without it"
command -v ffprobe >/dev/null && green "ffprobe"                                    || bad "ffprobe — ships with ffmpeg"

echo
echo "Project"
[ -f .env ] && green ".env"                                        || warn ".env absent — defaults are used; run: make setup"
[ -d backend/.venv ] && green "backend/.venv"                      || bad "backend/.venv — run: make install-backend"
[ -d frontend/node_modules ] && green "frontend/node_modules"      || bad "frontend/node_modules — run: cd frontend && pnpm install"
# The one that costs the most time when missing, because the error names a
# module rather than the step that produces it.
[ -f frontend/src/lib/api/generated.ts ] && green "frontend API types" \
    || bad "frontend/src/lib/api/generated.ts — gitignored and generated. Run: make types"

echo
echo "Infrastructure"
if command -v pg_isready >/dev/null && pg_isready -q -h localhost -p 5432 2>/dev/null; then
  green "Postgres :5432"
elif (exec 3<>/dev/tcp/localhost/5432) 2>/dev/null; then
  green "Postgres :5432"
else
  bad "Postgres :5432 — run: make infra"
fi

if (exec 3<>/dev/tcp/localhost/6379) 2>/dev/null; then green "Redis :6379"
else bad "Redis :6379 — run: make infra"; fi

if curl -sf -o /dev/null --max-time 3 http://localhost:9000/minio/health/live 2>/dev/null; then
  green "MinIO :9000"
else
  bad "MinIO :9000 — object storage. Uploads cannot work without it. Run: make infra"
fi

echo
echo "Running services"
curl -sf -o /dev/null --max-time 3 http://localhost:8000/health/live 2>/dev/null \
  && green "API :8000" || warn "API :8000 not running — make dev-all"
curl -sf -o /dev/null --max-time 3 http://localhost:3000/ 2>/dev/null \
  && green "frontend :3000" || warn "frontend :3000 not running — make dev-all"
# `[c]elery` rather than `celery`: pgrep -f matches full command lines, and a
# plain pattern also matches the shell running this script — which would report
# a worker that is not there. The bracket makes the literal text differ from
# what it matches.
if pgrep -f "[c]elery -A app.workers.celery_app" >/dev/null 2>&1; then
  green "ingest worker"
else
  warn "ingest worker not running — uploads will sit at 'probing' for ever. make dev-all"
fi

echo
if [ "$MISSING" -eq 1 ]; then
  echo "Something required is missing — see MISS above."
else
  echo "Everything required is present."
fi

cat <<'NOTE'

Two things that waste an afternoon if nobody says them:

  * Run pnpm from frontend/, never from the repository root. The root has no
    packageManager field, so corepack picks pnpm 11, which crashes on Node 24
    before printing anything. frontend/ pins pnpm 9.

  * An upload that stays on "Preparing…" for ever means the ingest worker is
    not running. Nothing errors, because nothing failed — the job is simply
    sitting in the queue with no one to take it.

NOTE
