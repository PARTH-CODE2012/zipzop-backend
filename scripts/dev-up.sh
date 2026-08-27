#!/usr/bin/env bash
# One command, three visible servers: the API, the ingest worker, and the
# frontend dev server — the same foreground commands documented in README.md,
# each in its own tmux window instead of typed into three separate terminals.
#
# Why tmux and not `make dev-all`: `dev-all` backgrounds all three and buries
# their output in /tmp/zipzop-*.log, which is the wrong tool the moment
# anything misbehaves — see README.md, "Running it in your own terminals".
# This is the same one-command convenience without hiding anything: every
# window's output is live on screen, and Ctrl-C in a window stops only that
# service.
#
# **Ports are resolved before anything starts** (scripts/ports.sh) and passed
# down to all three, so the frontend is told where the API actually landed
# rather than assuming 8000 the way it used to. That assumption is what broke
# this: another project on 8000 meant the editor talked to *it*, got
# `{"detail":"Not Found"}` from /health, and reported that it could not reach
# the server.
#
# Deliberately NOT run by anything automated — this starts long-lived
# processes and belongs in a terminal you are sitting at.

set -uo pipefail
cd "$(dirname "$0")/.."

SESSION=zipzop

# `bin` on POSIX, `Scripts` on Windows. Same resolution the Makefile does, for
# the same reason: a healthy virtualenv reported as a missing file.
VBIN=.venv/bin
[ -d backend/.venv/Scripts ] && VBIN=.venv/Scripts

if ! command -v tmux >/dev/null 2>&1; then
  cat <<'ERR'
tmux is not installed. Either:

    sudo apt install tmux

or run the three commands yourself, one per terminal — see README.md,
"Running it in your own terminals".
ERR
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "'$SESSION' is already running — attaching instead of starting a second copy."
  exec tmux attach -t "$SESSION"
fi

# ---------------------------------------------------------------- preflight
# Each of these fails later with a message that does not name the real cause: a
# missing generated.ts reads as a broken import, a missing venv as "uvicorn: not
# found". Checking here costs nothing and saves the twenty minutes.
MISSING=0
note() { printf '  \033[31mmissing\033[0m  %s\n' "$1"; MISSING=1; }

[ -d backend/.venv ]                        || note "backend/.venv — run: make install-backend"
[ -d frontend/node_modules ]                || note "frontend/node_modules — run: make install-frontend"
[ -f frontend/src/lib/api/generated.ts ]    || note "frontend API types — run: make types"
command -v ffmpeg >/dev/null                || note "ffmpeg — every upload fails without it: sudo apt install ffmpeg"

if [ "$MISSING" -eq 1 ]; then
  echo
  echo "Fix the above first, or run 'make doctor' for the full picture."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "  created .env from .env.example"
fi

# ------------------------------------------------------------------- ports
echo "Resolving ports…"
# shellcheck source=./ports.sh
source "$(dirname "$0")/ports.sh"
zz_resolve_ports || exit 1
echo "  API      :$API_PORT"
echo "  web      :$WEB_PORT"
echo

echo "Checking infrastructure and the schema before starting anything…"
make infra
make migrate
echo

# Every window gets the resolved ports through its own environment. `tmux
# new-session -e` would need tmux 3.2+, and the shell prefix works everywhere.
ENV_PREFIX="API_PORT=$API_PORT WEB_PORT=$WEB_PORT CORS_ORIGINS='$CORS_ORIGINS'"
ENV_PREFIX="$ENV_PREFIX NEXT_PUBLIC_API_BASE_URL='$NEXT_PUBLIC_API_BASE_URL'"
ENV_PREFIX="$ENV_PREFIX NEXT_PUBLIC_WS_URL='$NEXT_PUBLIC_WS_URL'"

pane() {  # pane <name> <what it runs>
  tmux new-window -t "$SESSION" -n "$1" \
    "$2; echo; echo '[$1 stopped — press any key to close this window]'; read -n1"
}

if [ "${ZZ_API_ALREADY_UP:-0}" = "1" ]; then
  echo "Not starting a second API — one is already answering on :$API_PORT."
  tmux new-session -d -s "$SESSION" -n worker \
    "cd backend && env $ENV_PREFIX ./$VBIN/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing,reconciliation --concurrency=2; echo; echo '[worker stopped — press any key to close this window]'; read -n1"
else
  tmux new-session -d -s "$SESSION" -n api \
    "cd backend && env $ENV_PREFIX ./$VBIN/uvicorn app.main:app --host 127.0.0.1 --port $API_PORT --reload; echo; echo '[api stopped — press any key to close this window]'; read -n1"
  pane worker "cd backend && env $ENV_PREFIX ./$VBIN/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing,reconciliation --concurrency=2"
fi

pane web "cd frontend && env $ENV_PREFIX pnpm dev --port $WEB_PORT"

tmux select-window -t "$SESSION:0"

cat <<EOF
Started — windows: $(tmux list-windows -t "$SESSION" -F '#{window_name}' | paste -sd' ')

  Switch window     Ctrl-b then 0 / 1 / 2  (or Ctrl-b then w to pick from a list)
  Detach            Ctrl-b then d           — leaves everything running
  Reattach later    tmux attach -t $SESSION
  Stop everything   make watch-stop         (or scripts/dev-down.sh)

  Editor    http://localhost:$WEB_PORT/editor/scratch
  API docs  http://localhost:$API_PORT/docs
  Health    http://localhost:$API_PORT/health
EOF

exec tmux attach -t "$SESSION"
