#!/usr/bin/env bash
# Bring up whatever infrastructure is not already running.
#
# Deliberately conditional rather than `docker compose up postgres redis minio`.
# Two setups exist in this project and both are supported:
#
#   * everything in Docker — the default, and what a fresh clone gets;
#   * Postgres and Redis from apt with only MinIO in Docker — which is what this
#     machine runs, because Docker Hub was unusable here for a while.
#
# Starting the compose Postgres on a machine that already has one listening on
# 5432 does not fall back gracefully: the container fails to bind and `make
# infra` errors out on a machine where the database was fine all along.
#
# So: check each port, start only what is absent, and say which path was taken.

set -uo pipefail
cd "$(dirname "$0")/.."

listening() { (exec 3<>/dev/tcp/localhost/"$1") 2>/dev/null; }

# `sg docker -c` covers the common case of a shell whose session predates the
# user being added to the docker group. Falls through to a plain call when the
# group is already active or `sg` is unavailable.
compose() {
  if docker compose "$@" >/dev/null 2>&1; then return 0; fi
  if command -v sg >/dev/null && sg docker -c "docker compose $*" >/dev/null 2>&1; then return 0; fi
  return 1
}

WANTED=()
listening 5432 && echo "  Postgres :5432 already up (native or container)" || WANTED+=(postgres)
listening 6379 && echo "  Redis    :6379 already up (native or container)" || WANTED+=(redis)

if curl -sf -o /dev/null --max-time 3 http://localhost:9000/minio/health/live 2>/dev/null; then
  echo "  MinIO    :9000 already up"
else
  # No apt equivalent, and required since M2 — uploads cannot work without it.
  WANTED+=(minio minio-init)
fi

if [ ${#WANTED[@]} -eq 0 ]; then
  echo
  echo "Everything is already running."
  exit 0
fi

echo
echo "Starting: ${WANTED[*]}"
if ! compose up -d "${WANTED[@]}"; then
  cat <<'ERR'

Could not reach the Docker daemon.

  * If this says "permission denied", your session predates being added to the
    docker group. Either log out and back in, or run the command directly with:

        sg docker -c "docker compose up -d minio minio-init"

  * If Docker is not installed and you only need Postgres and Redis, the native
    path in README.md covers those. MinIO has no apt equivalent and is required
    for uploads.
ERR
  exit 1
fi

echo
echo "  Postgres :5432 · Redis :6379 · MinIO :9000 (console :9001)"
