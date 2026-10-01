# Architecture

Multi-clinic voice-assistant backend (MVP). `Clinic` is the tenant.

## Layers

- `api/` — FastAPI routers, request/response mapping, DI via `api/deps.py`
  (`SessionDep`); domain→HTTP mapping in `api/errors.py`.
- `services/` — application use-cases, own transactions and scope checks;
  `availability.py` owns deterministic slot math (pure + testable).
- `repositories/` — thin typed queries per aggregate, always `clinic_id`-scoped.
- `models/` — 10 ORM entities (`clinics`, `specialties`, `departments`,
  `rooms`, `doctors`, `patients`, `appointments`, `clinic_schedules`,
  `doctor_schedules`, `schedule_exceptions`).
- `schemas/` — Pydantic v2 contracts per aggregate.
- `core/` — `Settings`, logging, domain errors (`NotFoundError`, `ConflictError`).
- `db/` — lazy singletons (engine/session factory, Redis client) + idempotent
  `seed_demo.py` (two clinics; never in migrations).

## Data flow

`router → service → repository → PostgreSQL` (Redis unused in MVP).

## Key decisions

- `GET /health` is a stable root contract; domain APIs live under `/api/v1`
  (patient) and `/api/v1/management`.
- Tenant isolation is explicit `clinic_id` filtering (no framework);
  cross-tenant access → `404`.
- Specialties are clinic-owned (names lowercased); rooms informational;
  one specialty + one nullable department per doctor.
- Appointments UTC `TIMESTAMPTZ`; clinic-local weekly schedules;
  `ends_at` computed server-side from the slot grid.
- Double booking prevented by partial unique index
  `uq_appointments_booked_slot WHERE status='booked'` + service pre-check.
- Schedule overrides: single `schedule_exceptions` table
  (`doctor_id NULL` = clinic-wide); precedence clinic day-off >
  clinic custom-hours ∩ doctor custom-hours > weekly template; doctor
  day-off > weekly template.
- No coverage gate; no auth/RBAC (synthetic data only); Redis reserved
  for Phase-2 slot caching.

## Configuration

12-factor via environment (see `.env.example`):
`DATABASE_URL`, `REDIS_URL`, `APP_ENV`, `LOG_LEVEL`.

## Testing

```text
backend/tests/
├── conftest.py        # TestClient + file-SQLite db_session/api_client fixtures
├── unit/              # pure slot math (intersect, grid, TZ, filters)
└── integration/       # lifecycle, isolation, exceptions, HTTP contracts
```

PG-only behavior (partial-index DDL, seed) verified manually against
`docker compose` Postgres; see README.

## Checks

Canonical scopes (also in `Makefile`, `AGENTS.md`, CI):

```bash
ruff check backend
ruff format --check backend
mypy backend/app
pytest
```
