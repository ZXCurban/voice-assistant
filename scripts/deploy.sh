#!/bin/sh
# Server-side CD script for the voice-assistant backend.
# Runs ON the server (e.g. /home/metrica/voice-assistant/deploy.sh).
# Idempotent: safe to re-run. Exits non-zero if the API is not healthy.
#
# Usage:
#   ./deploy.sh [branch]   # default branch: main
#
# What it does:
#   1. git pull (fast-forward only) for the deployed branch
#   2. docker compose build + up (db + redis + api)
#   3. waits for /health to answer 200
set -eu

BRANCH="${1:-main}"
API_PORT="${API_PORT:-8010}"
PROJECT="voice-assistant"

cd "$(dirname "$0")"

echo "[deploy] branch: $BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "[deploy] building and starting containers (project: $PROJECT)"
API_PORT="$API_PORT" docker compose -p "$PROJECT" up --build -d

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
