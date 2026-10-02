#!/bin/sh
# Container startup for the API service (runs as WORKDIR /code/backend):
# 1. migrate the schema (no-op when already current),
# 2. load the idempotent demo seed (skips what already exists),
# 3. exec the server command.
set -eu
alembic -c alembic.ini upgrade head
python -m app.db.seed_demo
exec "$@"
