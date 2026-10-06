#!/bin/sh
# Container startup for the API service (runs as WORKDIR /code/backend).
# Migrations and synthetic demo data are opt-in and must stay off in production.
set -eu
if [ "${APP_ENV:-local}" = "production" ] || [ "${APP_ENV:-local}" = "prod" ]; then
    if [ "${AUTO_MIGRATE:-false}" = "true" ] || [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
        echo "AUTO_MIGRATE and SEED_DEMO_DATA are forbidden in production" >&2
        exit 1
    fi
fi
if [ "${AUTO_MIGRATE:-false}" = "true" ]; then
    alembic -c alembic.ini upgrade head
fi
if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
    python -m app.db.seed_demo
fi
exec "$@"
