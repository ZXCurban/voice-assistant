# Voice Assistant Platform — Multi-Clinic Backend (MVP)

Backend foundation for a voice-assistant platform serving **multiple
independent clinics** from one PostgreSQL database. The backend is useful
on its own: the whole booking workflow works via Swagger/curl with no
frontend, LLM, STT, or TTS.

## Stack

- Python 3.13+, FastAPI, Pydantic v2
- PostgreSQL (SQLAlchemy 2.x async + asyncpg), Alembic
- Redis (short-lived, scoped assistant dialogue state)
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
docker compose up --build
# The local compose profile opts into migrations and synthetic demo seed.
# API: http://localhost:8000/docs ; liveness: /health ; readiness: /ready
# chat UI: http://localhost:8000/static/chat.html (deterministic text assistant)
```

Manual path (same result, step by step):

```bash
docker compose up --build -d db redis
alembic -c backend/alembic.ini upgrade head
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/app" \
  python -m app.db.seed_demo   # run with cwd=backend; idempotent
```

Local run without Docker:

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --app-dir backend
pytest
```

## Text assistant pipeline

`POST /api/v1/chat` and the bundled chat page currently use a deterministic
Russian intent/slot parser, normalizer, optional FRIDA decision policy,
`AssistantOrchestrator`, and Jinja2 response templates. No LLM server is
needed. `conversation_id` resumes Redis-backed workflow state across requests;
backend execution remains clinic-scoped and confirmation-gated for mutations.

The parser is a high-precision rules baseline, not a trained encoder model.
This repository has no STT/TTS integration or trained NLU weights yet.
FRIDA remains disabled by default because its first model load may download
roughly 1.2 GB; enable it only where the `frida_decisions` runtime and model
are provisioned. See `docs/architecture.md` and
`docs/assistant-architecture.md` for current coverage and limitations.

## API surfaces

`GET /health` stays at root. Everything else under `/api/v1`.

Patient/assistant (`/api/v1`): clinics, catalog
(`/clinics/{id}/specialties|departments|rooms|doctors`, `/doctors/{id}`),
patients, slots (`/slots`, `/doctors/{id}/slots`), appointments
(book/view/list/reschedule/cancel/complete).

Management (`/api/v1/management`): clinics, specialties, departments,
rooms, doctors, clinic/doctor schedules (create/update/remove),
schedule-exceptions (create/update/remove).

Conventions: collections nested as `/clinics/{id}/...`; singletons and
search take `?clinic_id=`; appointment listing requires `patient_id` or
`doctor_id`. Cross-tenant access returns `404`. Specialty names are
stored lowercased. Room codes are unique per clinic, not globally.
Doctor lists and appointments embed nested `doctor`/`specialty`/
`patient`/`room` context for voice presentation.

Voice-assistant mapping: see `docs/voice-map.md` (tool table, booking
flow, ambiguity and error handling). Reproducible live demo:
`make demo` (needs migrated + seeded DB and API on `:8000`).

Assistant orchestration: `app/assistant/` —
`AssistantRequest` in, `AssistantResult` out via `AssistantOrchestrator`
(stateless, reuses `app/services/*`; no SQL or duplicated business rules).
Contract details: `docs/assistant-architecture.md`.
Live orchestrator demo (real PG, no mocks):
`DATABASE_URL=... python -m app.assistant.demo` from `backend/`.

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
  is optional non-clinical free text. No EMR. Local demo mode has no auth;
  see the production boundary below.

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

## Security and deployment boundary

Local/demo mode has no authentication and should use synthetic data only.
In `APP_ENV=production`, versioned API requests require short-lived HMAC-signed
headers from a trusted channel gateway:

- `X-Trusted-Channel-Context`: base64url-encoded JSON claims;
- `X-Trusted-Channel-Signature`: lowercase hex HMAC-SHA256 over the exact
  encoded context value, using `CHANNEL_CONTEXT_SECRET` (at least 32 bytes).

Claims include `subject`, `role`, `clinic_id`, and `expires_at`. Patient chat
also requires a verified identity and a patient ID or verified phone plus name.
Patient records are accessed through chat only; they cannot call raw patient or
appointment CRUD APIs. Cross-clinic access returns 404. A gateway/identity
provider that verifies users and signs these claims is not included here, so
this boundary alone does not make the application ready for real patient data.

Production startup refuses demo seeding and automatic migrations. Deploy
migrations separately, keep `AUTO_MIGRATE=false` and `SEED_DEMO_DATA=false`,
and provide `CHANNEL_CONTEXT_SECRET` through a secret manager. `/health` is a
liveness check; `/ready` checks PostgreSQL and Redis. Rate limiting, audit
retention, TLS/secret rotation, real gateway integration, and clinical approval
of specialty-routing rules remain deployment prerequisites.

## Notes

- `tzdata` is a runtime dependency so `zoneinfo` works in slim images.
- `aiosqlite` is a dev-only dependency for integration tests.
- Redis stores short-lived, identity- and clinic-scoped dialogue state with
  per-conversation locking; it is required for `/ready` and multi-worker chat.

## License

MIT — see [LICENSE](LICENSE).
