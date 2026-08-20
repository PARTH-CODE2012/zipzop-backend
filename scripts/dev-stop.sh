#!/usr/bin/env bash
# Stop this project's dev processes — and **only** this project's.
#
# Two filters, and each one is a mistake that has actually happened here.
#
# 1. **The working directory.** The version this replaces was
#    `pkill -f "[n]ext dev"`, which matches a full command line and therefore
#    every Next dev server on the machine. On a laptop with more than one
#    checkout that is somebody else's work killed by a command that claimed to
#    stop yours — the same class of mistake as assuming port 8000 belongs to you.
#
# 2. **Ancestry.** `pgrep -f` matches command lines, and the shell running this
#    script has a command line too. Anything that so much as mentions the
#    pattern — a make recipe, an editor, the terminal that typed it — is a match
#    with the right working directory, and killing it takes the run down with it.
#    Writing the pattern as `[n]ext` only stops it matching *its own literal
#    text*; it does nothing about a caller that quotes it. So no ancestor of this
#    process is ever a target, whatever it matched.
#
# Never fails: stopping something already stopped is not an error.

set -uo pipefail
ZZ_ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Self, parent, parent's parent, all the way to init. None of these is ever a
# process this script started, so none of them is ever a process it may kill.
ancestors=" $$ "
walk=$$
while [ "$walk" -gt 1 ]; do
  walk=$(awk '{print $4}' "/proc/$walk/stat" 2>/dev/null) || break
  [ -z "$walk" ] && break
  ancestors="$ancestors$walk "
done

PATTERNS=(
  "uvicorn app.main:app"
  "celery -A app.workers.celery_app"
  "next dev"
  "next-server"
)

targets() {
  local pattern="$1" pid cwd
  while read -r pid; do
    [ -z "$pid" ] && continue
    case "$ancestors" in *" $pid "*) continue ;; esac
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
    case "$cwd" in
      "$ZZ_ROOT"|"$ZZ_ROOT"/*) echo "$pid" ;;
      *) printf '  left alone: pid %s (%s) — not this project\n' "$pid" "$cwd" >&2 ;;
    esac
  done < <(pgrep -f "$pattern" 2>/dev/null)
}

stopped=0
for pattern in "${PATTERNS[@]}"; do
  while read -r pid; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
  done < <(targets "$pattern")
done

# A moment for SIGTERM to land, then insist. uvicorn --reload spawns a child
# that outlives a kill aimed at the parent, and an orphan keeps the port — which
# is the thing this whole system exists to avoid.
if [ "$stopped" -gt 0 ]; then
  sleep 1
  for pattern in "${PATTERNS[@]}"; do
    while read -r pid; do
      [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
    done < <(targets "$pattern" 2>/dev/null)
  done
fi

echo "stopped $stopped process(es) from $ZZ_ROOT"
exit 0
