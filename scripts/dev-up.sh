#!/usr/bin/env bash
# One command, three visible servers: the API, the ingest worker, and the
# frontend dev server — the same foreground commands documented in README.md,
# each in its own tmux pane instead of typed into three separate terminals.
#
# Why tmux and not `make dev-all`: `dev-all` backgrounds all three and buries
# their output in /tmp/zipzop-*.log, which is the wrong tool the moment
# anything misbehaves — see README.md, "Running it in your own terminals".
# This is the same one-command convenience without hiding anything: every
# pane's output is live on screen, and Ctrl-C in a pane stops only that
# service.
#
# Deliberately NOT run by anything automated — this starts long-lived
# processes and belongs in a terminal you are sitting at.

set -uo pipefail
cd "$(dirname "$0")/.."

SESSION=zipzop

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

echo "Checking infrastructure and the schema before starting anything…"
make infra
make migrate
echo

tmux new-session -d -s "$SESSION" -n api \
  "cd backend && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload; echo; echo '[api stopped — press any key to close this pane]'; read -n1"

tmux new-window -t "$SESSION" -n worker \
  "cd backend && ./.venv/bin/celery -A app.workers.celery_app worker --loglevel=INFO -Q ingest,analysis,render,billing --concurrency=2; echo; echo '[worker stopped — press any key to close this pane]'; read -n1"

tmux new-window -t "$SESSION" -n web \
  "cd frontend && pnpm dev --port 3000; echo; echo '[web stopped — press any key to close this pane]'; read -n1"

tmux select-window -t "$SESSION:api"

cat <<EOF
Started — three windows: api, worker, web.

  Switch window     Ctrl-b then 0 / 1 / 2  (or Ctrl-b then w to pick from a list)
  Detach            Ctrl-b then d           — leaves everything running
  Reattach later    tmux attach -t $SESSION
  Stop everything    make watch-stop         (or scripts/dev-down.sh)

Editor: http://localhost:3000/editor/scratch
EOF

exec tmux attach -t "$SESSION"
