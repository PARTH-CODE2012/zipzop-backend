#!/bin/sh
# Every Part B check the local staging stack can answer.
#
#   1. checks.py, from the public network — what an outsider can reach;
#   2. from inside: worker and API egress, Redis authentication, and the
#      proxy's access log;
#   3. with SCANS=1, the OWASP ZAP baseline and sqlmap (reports in reports/).
#
# Exit status: the number of failed checks.
set -u
cd "$(dirname "$0")"
failures=0
fail() { echo "FAIL  $1"; failures=$((failures + 1)); }
pass() { echo "PASS  $1"; }

# Every run starts as a new visitor would: no rate-limit counters left over
# from the previous run (the signup allowance is hourly).
docker compose exec -T redis sh -c "redis-cli --scan --pattern 'ratelimit:*' | xargs -r redis-cli del" >/dev/null

echo "== from the public network"
out=$(docker compose --profile check run --rm -T checks 2>&1)
status=$?
echo "$out"
failures=$((failures + status))
ticket=$(echo "$out" | sed -n 's/^TICKET_FOR_LOG_CHECK=//p' | tail -1)

echo
echo "== from inside the stack"

# Egress. `data` is an internal network: a worker can reach the database, the
# queue and storage through the edge, and nothing else — not the internet, not
# the instance metadata endpoint (docs/07-security.md §6.4).
probe='
import socket, sys
target, port, want = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    socket.create_connection((target, port), timeout=4).close()
    got = "open"
except OSError as exc:
    got = "closed"
print(got)
sys.exit(0 if got == want else 1)
'
for service in worker api; do
  for case in "1.1.1.1 443 closed" "169.254.169.254 80 closed" "pypi.org 443 closed" \
              "media.zipzop.test 443 open" "postgres 5432 open" "redis 6379 open"; do
    set -- $case
    if docker compose exec -T "$service" python -c "$probe" "$1" "$2" "$3" >/dev/null 2>&1; then
      pass "$service → $1:$2 is $3"
    else
      fail "$service → $1:$2 should be $3"
    fi
  done
done

# Redis refuses a client that does not authenticate.
reply=$(docker compose exec -T redis sh -c 'env -u REDISCLI_AUTH redis-cli ping' 2>&1)
case "$reply" in
  *NOAUTH*) pass "redis refuses an unauthenticated client ($reply)" ;;
  *) fail "redis answered an unauthenticated ping: $reply" ;;
esac

# The proxy's access log has the socket's request line, and not its ticket.
logs=$(docker compose logs caddy 2>&1)
if [ -z "$ticket" ]; then
  fail "no ticket came back from checks.py to look for"
elif echo "$logs" | grep -q -- "$ticket"; then
  fail "the access log holds a WebSocket ticket"
elif echo "$logs" | grep -q 'ticket=REDACTED'; then
  pass "the access log records /v1/ws?ticket=REDACTED, never the ticket"
else
  fail "no WebSocket request line found in the access log"
fi

if [ "${SCANS:-0}" = "1" ]; then
  echo
  echo "== scans"
  mkdir -p reports
  # Docker on Windows needs a Windows path for a bind mount (`pwd -W` under
  # Git Bash); everywhere else plain `pwd` is right.
  here="$(pwd -W 2>/dev/null || pwd)"
  for target in https://app.zipzop.test https://api.zipzop.test/health/live; do
    name=$(echo "$target" | sed 's#https://##; s#[/.]#-#g')
    # On `public`, where an outsider would scan from.
    MSYS_NO_PATHCONV=1 docker run --rm --network zipzop-staging_public -v "$here/reports:/zap/wrk:rw" \
      zaproxy/zap-stable@sha256:781a2bdaea47324e7bab583e2263f21d257b0aee61ed51521a5be45f5f5081ef \
      zap-baseline.py -t "$target" -I -m 2 -r "zap-$name.html" -J "zap-$name.json" \
      > "reports/zap-$name.log" 2>&1
    echo "ZAP baseline $target: $(grep -E '^FAIL-NEW|^WARN-NEW|^PASS' "reports/zap-$name.log" | tr '\n' ' ')"
  done
  # A fresh signup allowance: the farming check above spent this hour's.
  docker compose exec -T redis sh -c "redis-cli --scan --pattern 'ratelimit:*' | xargs -r redis-cli del" >/dev/null
  docker compose --profile check run --rm -T --entrypoint sh checks \
    -c 'pip install -q sqlmap==1.10.9 && python /checks/sqlmap_targets.py' \
    > reports/sqlmap.log 2>&1
  echo "sqlmap: $(grep -E '^SQLMAP' reports/sqlmap.log | tr '\n' ' ')"
fi

echo
echo "$failures failure(s)"
exit "$failures"
