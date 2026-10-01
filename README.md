# Voice Assistant Platform — Multi-Clinic Backend (MVP)

Backend foundation for a voice-assistant platform serving **multiple
independent clinics** from one PostgreSQL database. The backend is useful
on its own: the whole booking workflow works via Swagger/curl with no
frontend, LLM, STT, or TTS.

## Stack

- Python 3.13+, FastAPI, Pydantic v2
- PostgreSQL (SQLAlchemy 2.x async + asyncpg), Alembic
- Redis (wired, unused by domain logic in MVP — see below)
- pytest, Ruff, mypy (strict), Docker / Docker Compose, GitHub Actions

## Structure

```text
backend/
  app/
    api/v1/            # patient/assistant routers + management/ subpackage
    api/errors.py       # NotFoundError→404, ConflictError→409, ValueError→422
    core/              # settings, logging, domain errors
    db/                # engine/session + Redis holders, seed_demo.py
    models/            # 10 ORM entities (all but clinics carry clinic_id)
    schemas/           # Pydantic contracts per aggregate
    repositories/      # thin queries, always scoped by clinic_id
    services/          # use-cases; availability.py owns slot math
    main.py            # create_app(); GET /health at root
  tests/
    unit/              # pure slot math, no infra
    integration/       # lifecycle/isolation/exceptions/HTTP (file-SQLite)
  alembic/versions/    # 0001_multi_clinic_platform
```

Layering: `api → services → repositories → models/db`. No imports upward.

## Quickstart

```bash
cp .env.example .env
docker compose up --build -d db redis
alembic -c backend/alembic.ini upgrade head
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/app" \
  python -m app.db.seed_demo   # run with cwd=backend; idempotent
# API: http://localhost:8000/docs ; health: http://localhost:8000/health
```

Local run without Docker:

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --app-dir backend
pytest
```

## API surfaces

`GET /health` stays at root. Everything else under `/api/v1`.

Patient/assistant (`/api/v1`): clinics, catalog
(`/clinics/{id}/specialties|departments|rooms|doctors`, `/doctors/{id}`),
patients, slots (`/slots`, `/doctors/{id}/slots`), appointments
(book/view/list/reschedule/cancel/complete).

Management (`/api/v1/management`): clinics, specialties, departments,
rooms, doctors, clinic/doctor schedules, schedule-exceptions.

Conventions: collections nested as `/clinics/{id}/...`; singletons and
search take `?clinic_id=`; appointment listing requires `patient_id` or
`doctor_id`. Cross-tenant access returns `404`. Specialty names are
stored lowercased. Room codes are unique per clinic, not globally.

## Core rules

- **Tenant isolation:** every repository query filters by `clinic_id`.
- **Time:** appointments stored UTC (`TIMESTAMPTZ`); `clinics.timezone`
  mandatory IANA; weekly schedules are clinic-local wall-clock.
- **Slots:** `clinic hours ∩ doctor hours ∩ exceptions − booked`,
  aligned fixed grid (`slot_minutes` per doctor schedule). Pure functions
  in `services/availability.py`.
- **Double booking:** partial unique index
  `uq_appointments_booked_slot ... WHERE status='booked'` is the final
  arbiter; `IntegrityError` → `409`. Cancelling frees the instant.
- **Rooms** are informational (no capacity checks). Appointment `reason`
  is optional non-clinical free text. No EMR, no auth in MVP.

## Checks

```bash
ruff check backend && ruff format --check backend
mypy backend/app
pytest
```

Or via Make: `make lint`, `make typecheck`, `make test`.

Integration tests use file-SQLite (works in CI without infra);
Postgres-specific behavior (partial indexes, seed) is verified manually
against `docker compose` Postgres.

## Migrations & seed

```bash
alembic -c backend/alembic.ini revision --autogenerate -m "..."
alembic -c backend/alembic.ini upgrade head
```

Never put demo data in migrations — `app/db/seed_demo.py` only
(idempotent, two clinics: Warsaw + Lisbon).

## Security note (MVP has no auth)

Anyone with network access can list/book/cancel. **Synthetic data only.**
Production needs authentication, per-clinic roles, rate limiting, audit
log, and non-sequential IDs before handling real personal/medical data.

## Notes

- `tzdata` is a runtime dependency so `zoneinfo` works in slim images.
- `aiosqlite` is a dev-only dependency for integration tests.
- Redis client is wired (`db/redis.py`) but no service uses it yet;
  slot caching (60s TTL + invalidation on writes) is a documented
  Phase-2 step, not implemented.
