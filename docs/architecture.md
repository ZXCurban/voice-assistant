# Architecture

Skeleton only — no business logic.

## Layers

- `api/` — FastAPI routers, request/response mapping, DI via `api/deps.py`.
- `services/` — application use-cases (to be added per feature).
- `repositories/` — SQLAlchemy queries per aggregate (to be added per feature).
- `models/` — ORM entities inheriting `Base` (currently empty).
- `schemas/` — Pydantic contracts (currently `HealthResponse` only).
- `core/` — `Settings` (pydantic-settings) and logging.
- `db/` — lazy singletons: async engine/session factory, Redis client.

## Data flow

`router → service → repository → PostgreSQL/Redis`

## Configuration

12-factor via environment (see `.env.example`):
`DATABASE_URL`, `REDIS_URL`, `APP_ENV`, `LOG_LEVEL`.

## Testing

Smoke tests assert the app boots and `/health` works without live infra.
Feature tests should mock services/repositories, integration tests should
use ephemeral Postgres/Redis (not included in skeleton).
