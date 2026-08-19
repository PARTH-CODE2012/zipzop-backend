#!/usr/bin/env bash
# Stops what dev-up.sh started. Never touches Postgres, Redis or MinIO —
# those are containers, and `make down` is what stops those.

set -uo pipefail
SESSION=zipzop

if ! command -v tmux >/dev/null 2>&1 || ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "No '$SESSION' session running."
  exit 0
fi

# Ctrl-C every pane first, so uvicorn/celery/next shut down cleanly instead of
# being killed mid-write.
for window in $(tmux list-windows -t "$SESSION" -F '#{window_index}'); do
  tmux send-keys -t "$SESSION:$window" C-c
done
sleep 1
tmux kill-session -t "$SESSION" 2>/dev/null || true
echo "Stopped."
