#!/usr/bin/env bash
# The ports the development stack runs on — resolved once, in one place.
#
# **Why this file exists.** 8000 and 3000 are the two most contested ports on a
# developer's machine: every Python service and every Next.js app defaults to
# them. Another project holding one is not an unusual event, it is the normal
# state of a laptop with more than one checkout, and what it produced here was
# a stack that failed in three different ways depending on which half won:
#
#   * the API exits with `[Errno 98] address already in use` — clear enough;
#   * the *frontend* silently talks to whatever else is on 8000. `GET /health`
#     answers `{"detail":"Not Found"}` and the sign-in panel says it cannot
#     reach the server, which sends you looking at code that is fine;
#   * `pnpm dev --port 3000` fails or slides to another port, and then the API's
#     CORS list — which names 3000 — rejects every request from it.
#
# So the defaults move off the contested pair, **and** every entry point resolves
# through here rather than repeating a number. A port that is taken anyway is
# stepped over and announced, and the frontend is told where the API actually
# landed instead of assuming.
#
# Sourced, never executed:  source scripts/ports.sh && zz_resolve_ports

# 8123 / 3123: one-two-three, easy to type and to remember, and clear of every
# framework default worth naming — 3000/3001 (Next, Rails), 5000 (Flask, and
# AirPlay on macOS), 5173 (Vite), 8000/8080 (Django, Python, Tomcat), 9000
# (MinIO, php-fpm). Override both in .env.
ZZ_DEFAULT_API_PORT=8123
ZZ_DEFAULT_WEB_PORT=3123

#: How far to walk when the wanted port is taken. Twenty is far more than a
#: machine with a handful of projects on it will ever need, and it stops a
#: misconfigured host from scanning its way to 65535.
ZZ_PORT_SEARCH_LIMIT=20

zz_listening() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }

# Best-effort "who is on this port", for the message only. Never fails the run:
# the answer is a courtesy, not a decision.
zz_port_holder() {
  local port=$1 line pid name cwd
  line=$(ss -ltnp 2>/dev/null | grep -E "[:.]${port}[[:space:]]" | head -1)
  if [ -z "$line" ]; then echo "another process"; return; fi

  pid=$(sed -n 's/.*pid=\([0-9]\+\).*/\1/p' <<<"$line" | head -1)
  name=$(sed -n 's/.*users:((\"\([^"]*\)\".*/\1/p' <<<"$line" | head -1)
  [ -n "$pid" ] && cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null)

  # The working directory is the part that actually identifies it. "python" tells
  # you nothing; "…/STUDYAI_PHASES" tells you which project to go and stop.
  if [ -n "${cwd:-}" ]; then
    echo "${name:-a process} (pid ${pid:-?}) in ${cwd}"
  else
    echo "${name:-a process}${pid:+ (pid $pid)}"
  fi
}

# Is *our* API already on this port? A stack left running from an earlier
# session should be reused, not stepped around — otherwise a second `make watch`
# starts a second API on 8124 while the frontend keeps talking to the first.
zz_is_zipzop_api() {
  curl -sf --max-time 2 "http://127.0.0.1:$1/openapi.json" 2>/dev/null \
    | grep -q '"title":"ZipZop API"'
}

# Read the wanted ports from .env, falling back to the defaults above. Only
# these two variables are read: sourcing a whole .env into the shell would
# execute whatever quoting mistake is in it.
zz_load_ports() {
  local env_file="${ZZ_ENV_FILE:-$ZZ_ROOT/.env}"
  local from_file
  if [ -f "$env_file" ]; then
    from_file=$(sed -n 's/^[[:space:]]*API_PORT[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' "$env_file" | tail -1)
    [ -n "$from_file" ] && API_PORT=${API_PORT:-$from_file}
    from_file=$(sed -n 's/^[[:space:]]*WEB_PORT[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' "$env_file" | tail -1)
    [ -n "$from_file" ] && WEB_PORT=${WEB_PORT:-$from_file}
  fi
  API_PORT=${API_PORT:-$ZZ_DEFAULT_API_PORT}
  WEB_PORT=${WEB_PORT:-$ZZ_DEFAULT_WEB_PORT}
  export API_PORT WEB_PORT
}

# Everything downstream is derived, never written down twice.
#
# CORS carries both spellings of the same origin: a browser sent to
# `127.0.0.1` and one sent to `localhost` present *different* Origin headers,
# and a list with only one of them rejects half the ways of opening the app.
zz_export_urls() {
  export NEXT_PUBLIC_API_BASE_URL="http://localhost:${API_PORT}/v1"
  export NEXT_PUBLIC_WS_URL="ws://localhost:${API_PORT}/v1/ws"
  export CORS_ORIGINS="http://localhost:${WEB_PORT},http://127.0.0.1:${WEB_PORT}"
  export E2E_APP_URL="http://localhost:${WEB_PORT}"
  export E2E_API_URL="http://localhost:${API_PORT}"
}

# Find a free port at or after `$1`, or return 1 having said why.
zz_free_port_from() {
  local port=$1 tried=0
  while zz_listening "$port"; do
    port=$((port + 1))
    tried=$((tried + 1))
    if [ "$tried" -gt "$ZZ_PORT_SEARCH_LIMIT" ]; then
      echo "no free port between $1 and $port" >&2
      return 1
    fi
  done
  echo "$port"
}

# The one callers use. Resolves both ports, stepping over anything already
# there, and exports every URL derived from them.
zz_resolve_ports() {
  zz_load_ports

  if zz_listening "$API_PORT"; then
    if zz_is_zipzop_api "$API_PORT"; then
      echo "  API      :$API_PORT — a ZipZop API is already there, reusing it"
      ZZ_API_ALREADY_UP=1
    else
      local wanted=$API_PORT
      API_PORT=$(zz_free_port_from $((API_PORT + 1))) || return 1
      echo "  API      :$wanted is taken by $(zz_port_holder "$wanted")"
      echo "           → using :$API_PORT instead"
    fi
  fi

  if zz_listening "$WEB_PORT"; then
    local wanted=$WEB_PORT
    WEB_PORT=$(zz_free_port_from $((WEB_PORT + 1))) || return 1
    echo "  web      :$wanted is taken by $(zz_port_holder "$wanted")"
    echo "           → using :$WEB_PORT instead"
  fi

  export API_PORT WEB_PORT
  zz_export_urls
}

# `ZZ_ROOT` is the repository root, so a caller can source this from anywhere.
ZZ_ROOT=${ZZ_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
export ZZ_ROOT
