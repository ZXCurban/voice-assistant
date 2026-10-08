# Architecture

## Assistant request flow

`POST /api/v1/chat` now runs text through a deterministic parser, normalizer,
optional FRIDA policy, `AssistantOrchestrator`, and response templates. The
existing response fields stay `conversation_id`, `message`, and `model`; the
new path reports `model: deterministic`.

This repository contains no STT/TTS adapters, trained NLU checkpoint, or
installed FRIDA runtime. NLU is currently a Russian high-precision rules
baseline with typed intent and slot confidence, intended to establish the
fine-tuning contract. It does not cover the full range of speech variations.
FRIDA is optional because its runtime downloads about 1.2 GB at first use.
When enabled, it receives normalized JSON and cannot access backend services.

Backend responses are rendered by
`backend/app/assistant/templates/responses.ru.j2`; no generative model runs
in the chat path. No LLM fallback is configured because deterministic
responses cover the current backend events.

The text workflow supports multi-turn booking, status lookup, cancellation,
and rescheduling through the existing services. Patient identity is synthetic
and inferred only in local demo mode; production chat needs a trusted signed
channel context. Ambiguous medical concerns are not clinically triaged.

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
  `seed_demo.py` (four Russian demo clinics; never in migrations).

## Data flow

`router → service → repository → PostgreSQL`; assistant state is stored in
Redis with a short TTL and scoped by signed principal + clinic.

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
- The local/demo profile is unauthenticated and synthetic-data-only. In the
  production profile, a middleware verifies short-lived HMAC context claims
  from a trusted gateway and enforces role/clinic scope. A real gateway and
  identity provider are not included.
- Redis is used for assistant conversation state and readiness checks; domain
  data remains in PostgreSQL.

## Configuration

12-factor via environment (see `.env.example`):
`DATABASE_URL`, `REDIS_URL`, `APP_ENV`, `LOG_LEVEL`,
`CHANNEL_CONTEXT_SECRET`, `CONVERSATION_STATE_TTL_S`, `AUTO_MIGRATE`, and
`SEED_DEMO_DATA`. Production requires a 32-byte signing secret and forbids
startup migrations or demo seeding. `/health` is liveness; `/ready` checks DB
and Redis.

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
