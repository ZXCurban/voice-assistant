#!/bin/sh
# Server-side CD script for the voice-assistant backend.
# Runs ON the server from the repo checkout, e.g.:
#   API_PORT=8010 ./scripts/deploy.sh main
# Idempotent: safe to re-run. Exits non-zero if the API is not healthy.
# Skips the rebuild when the branch tip has not changed (but still ensures
# the stack is up, e.g. after a reboot).
#
# Usage:
#   ./scripts/deploy.sh [branch]   # default branch: main
#
# What it does:
#   1. git pull (fast-forward only) for the deployed branch
#   2. docker compose build + up (db + redis + api), or plain up if unchanged
#   3. waits for /health to answer 200
set -eu

BRANCH="${1:-main}"
API_PORT="${API_PORT:-8010}"
PROJECT="voice-assistant"

# Repo root (this script lives in scripts/).
cd "$(dirname "$0")/.."

echo "[deploy] branch: $BRANCH"
if [ -n "$(git status --porcelain)" ]; then
    echo "[deploy] ERROR: working tree is dirty, refusing to pull" >&2
    git status --short >&2
    exit 1
fi
git fetch origin
git checkout "$BRANCH"
BEFORE="$(git rev-parse HEAD)"
git pull --ff-only origin "$BRANCH"
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "[deploy] no new commits ($AFTER), ensuring the stack is up"
    # shellcheck disable=SC2086
    API_PORT="$API_PORT" docker compose -p "$PROJECT" up -d
else
    echo "[deploy] $BEFORE -> $AFTER, building and starting (project: $PROJECT)"
    # shellcheck disable=SC2086
    API_PORT="$API_PORT" docker compose -p "$PROJECT" up --build -d
fi

echo "[deploy] waiting for API health on port $API_PORT"
ok=0
for i in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
        ok=1
        break
    fi
    sleep 2
done

if [ "$ok" -ne 1 ]; then
    echo "[deploy] ERROR: API did not become healthy in time" >&2
    docker compose -p "$PROJECT" ps
    docker compose -p "$PROJECT" logs --tail=50 api
    exit 1
fi

echo "[deploy] OK: $(curl -s "http://127.0.0.1:${API_PORT}/health")"
docker compose -p "$PROJECT" ps
