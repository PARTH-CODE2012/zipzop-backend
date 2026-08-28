#!/usr/bin/env bash
#
# Are the Razorpay keys real, and can the account charge the currency we price in?
#
# Two questions that block M6 and that nothing else can answer, because both are
# properties of the *account* rather than of our code:
#
#   1. do the keys authenticate at all;
#   2. will Razorpay accept an order in USD — $3.99 is a dollar price on an
#      Indian processor, and international acceptance is a separate activation
#      on the account, not an API capability.
#
# Read-only by default. The currency probe creates a test-mode order, which is
# why it is opt-in rather than the default: it writes something, even if what it
# writes is inert and costs nothing.
#
# Usage:
#   scripts/razorpay-check.sh              # authenticate only
#   scripts/razorpay-check.sh --currency   # also probe INR and USD
#
# The keys come from .env, which is gitignored. Nothing here prints a secret.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "no .env — copy it from .env.example and fill in the Razorpay pair" >&2
  exit 1
fi

# Read the two values without sourcing the file: .env is not a shell script, and
# a stray backtick or $( in any unrelated value would be executed if it were.
KEY_ID="$(grep -E '^RAZORPAY_KEY_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
KEY_SECRET="$(grep -E '^RAZORPAY_KEY_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"

if [[ -z "$KEY_ID" || -z "$KEY_SECRET" ]]; then
  echo "RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET is empty in .env" >&2
  exit 1
fi

# The id is public by design — it ships in the browser bundle — so echoing it is
# fine. The secret is never printed, not even partially.
case "$KEY_ID" in
  rzp_test_*) MODE="test" ;;
  rzp_live_*) MODE="LIVE" ;;
  *)          MODE="unrecognised prefix" ;;
esac
echo "key id : $KEY_ID"
echo "mode   : $MODE"
[[ "$MODE" == "LIVE" ]] && echo "  ⚠️  these are live keys — the currency probe would create a real order"
echo

# ---------------------------------------------------------------- authenticate

echo "→ authenticating…"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -u "$KEY_ID:$KEY_SECRET" \
  'https://api.razorpay.com/v1/payments?count=1' || echo 000)"

case "$CODE" in
  200) echo "  ok — the keys authenticate" ;;
  401) echo "  FAILED — 401, the key id and secret do not match" >&2; exit 1 ;;
  000) echo "  FAILED — could not reach api.razorpay.com" >&2; exit 1 ;;
  *)   echo "  FAILED — HTTP $CODE" >&2; exit 1 ;;
esac

if [[ "${1:-}" != "--currency" ]]; then
  echo
  echo "run with --currency to also check which currencies the account accepts"
  exit 0
fi

# ------------------------------------------------------------------- currency

# An order is the cheapest object that carries a currency and is rejected when
# the account cannot take it. Amounts are in minor units: 399 paise / 399 cents.
probe() {
  local currency="$1" body response
  body="{\"amount\":399,\"currency\":\"$currency\",\"receipt\":\"zipzop-currency-probe\"}"
  response="$(curl -s -u "$KEY_ID:$KEY_SECRET" -H 'Content-Type: application/json' \
    -d "$body" 'https://api.razorpay.com/v1/orders' || true)"

  if grep -q '"id"' <<<"$response"; then
    echo "  $currency : accepted"
  else
    echo "  $currency : REFUSED — $(sed -n 's/.*"description":"\([^"]*\)".*/\1/p' <<<"$response")"
  fi
}

echo
echo "→ probing currencies (creates test-mode orders, which cost nothing)…"
probe INR
probe USD
echo
echo "If USD is refused, the price is a dollar figure the account cannot take."
echo "Either international acceptance is activated on the Razorpay account, or"
echo "the \$3.99 is priced in INR instead — see docs/13-mvp-direction.md §3.4."
