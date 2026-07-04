#!/usr/bin/env bash
# Smoke-tests a running PennyWise API. Used by the deploy and rollback
# workflows right after a rollout, and safe to run locally:
#
#   scripts/smoke.sh http://localhost:8000
#   scripts/smoke.sh https://api.pennywise.app
#
# Exits non-zero with the name of the first failing check, so CI logs show
# exactly what broke instead of a bare curl exit code.
set -euo pipefail

URL="${1:?Usage: smoke.sh <base-url>}"
URL="${URL%/}"
RETRIES="${SMOKE_RETRIES:-12}"
SLEEP_S="${SMOKE_SLEEP_S:-10}"

fail() {
  echo "SMOKE FAIL: $1" >&2
  exit 1
}

# Retries only the initial readiness wait (ECS may still be draining the
# old task / starting the new one); every other check runs once readiness
# is up, since a still-failing check past that point is a real regression.
wait_ready() {
  for i in $(seq 1 "$RETRIES"); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/health/ready" || true)
    if [ "$code" = "200" ]; then
      echo "OK  /health/ready (attempt $i)"
      return 0
    fi
    echo "... /health/ready attempt $i: got ${code:-<no response>}, retrying in ${SLEEP_S}s"
    sleep "$SLEEP_S"
  done
  fail "/health/ready never returned 200"
}

expect_status() {
  local method="$1" path="$2" want="$3" desc="$4"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$URL$path")
  if [ "$code" != "$want" ]; then
    fail "$desc — expected $want, got $code ($method $path)"
  fi
  echo "OK  $desc"
}

expect_header() {
  local path="$1" header="$2" desc="$3"
  if ! curl -sI "$URL$path" | tr -d '\r' | grep -qi "^${header}:"; then
    fail "$desc — missing header $header"
  fi
  echo "OK  $desc"
}

# Catches config bugs that produce a syntactically fine redirect to Google
# but with a broken/empty payload (e.g. a Terraform expression that
# collapses to "" when no custom domain is configured) — none of the unit
# tests exercise the real deployed env-var substitution, and a blank
# redirect_uri fails silently until a human clicks "Sign in with Google"
# and hits Google's own invalid_request page. Doesn't complete a real
# OAuth flow (needs live credentials, not appropriate for an automated
# smoke test) — just verifies the request we send Google is well-formed.
expect_oauth_redirect_valid() {
  local location redirect_uri
  # GET, not HEAD (-I): this route only registers GET, HEAD gets a 405. -D -
  # dumps response headers to stdout without following the redirect (-L is
  # NOT passed), which is what we want — inspect it, don't chase it.
  location=$(curl -s -o /dev/null -D - "$URL/api/auth/google/start" | tr -d '\r' | grep -i '^location:' | sed -E 's/^[Ll]ocation: *//' || true)
  if [ -z "$location" ]; then
    fail "OAuth start (/api/auth/google/start) returned no redirect Location header"
  fi
  case "$location" in
    https://accounts.google.com/*) ;;
    *) fail "OAuth start redirected somewhere unexpected: $location" ;;
  esac
  redirect_uri=$(echo "$location" | grep -oE 'redirect_uri=[^&]*' | sed 's/redirect_uri=//')
  if [ -z "$redirect_uri" ]; then
    fail "OAuth redirect_uri sent to Google is empty — Google rejects this outright (invalid_request); check GOOGLE_REDIRECT_URI"
  fi
  echo "OK  OAuth redirect to Google carries a non-empty redirect_uri"
}

wait_ready
expect_status GET  /health           200 "liveness probe"
expect_status GET  /login            200 "login page renders"
expect_status GET  /api/auth/me      401 "auth is enforced (no token -> 401)"
expect_status POST /api/recommendations 401 "recommendations requires auth"
expect_header /health x-request-id   "request-id middleware active"
expect_oauth_redirect_valid

echo "All smoke checks passed against $URL"
