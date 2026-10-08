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

The default parser is a high-precision rules baseline. A trained NLU can take
over the chat turns (see «Local NLU» below); this repository has no STT/TTS
integration.
FRIDA remains disabled by default because its first model load may download
roughly 1.2 GB; enable it only where the `frida_decisions` runtime and model
are provisioned. See `docs/architecture.md` and
`docs/assistant-architecture.md` for current coverage and limitations.

## Local NLU (ml-training models, optional)

`ml-training` ships a two-stage Russian NLU: a ruBERT **intent classifier**
(15 intents, calibrated confidence) and a rut5 **slot extractor**
(`specialty`, `date`, `time`, `city`, `phone`, `full_name`, …). With
`NLU_ENABLED=true` the chat (`POST /api/v1/chat`) lets them answer first:

```
message ─▶ fastpath smalltalk (first turn)
        ─▶ emergency regex (112) ─▶ NLU (intent + slots, context = previous reply)
        ─▶ DialogueManager (state machine) ─▶ AssistantOrchestrator tools
        ─▶ templated reply  (response.model = "nlu:<version>")
        └─ NLU not confident on an idle turn ─▶ rule-based pipeline above
```

Who answers a turn: the NLU takes it unless (a) the identity is tenant-locked
(trusted channel with signed claims: always the rule-based pipeline, so clinic
and patient scoping is enforced in one place), or (b) the rule-based pipeline is
in the middle of a dialogue (it started one after the NLU handed over an
unconfident turn, and finishes it). `response.model` tells which one answered:
`nlu:<version>` or `deterministic`. NLU dialogue state is in process memory;
the Redis-backed state belongs to the rule-based pipeline.

The dialogue manager is deterministic: it asks for what is missing
(specialty → date → city → slot → patient → phone/name), never invents
facts, and every mutation goes through the orchestrator's confirm step
(the preview is shown, only «да» books/cancels/reschedules). Replies are the
exact phrases the models were trained on, so the previous reply is a valid
NLU context. Typos and model slips are covered by deterministic fallbacks
(phone/name verification against the typed text, fuzzy specialty and
«завтра», doctor surnames in any Russian case, `+7` normalisation (trunk `8…`, bare mobiles).

The trained weights are in the repository: `models/nlu/` has the
`ml-training` layout (`intent/pytorch`, `slots/pytorch`, `intent/calibration.json`)
with every `model.safetensors` cut into `*.partNN` files under 90 MB (GitHub's
limit is 100 MB) and a `SHA256SUMS`. The app joins and verifies them on first
start (or run `make nlu-weights`), so `git clone` is enough.

```bash
pip install ".[nlu]"        # torch + transformers + sentencepiece (CPU wheel is enough)
make nlu-weights            # optional: join + verify the weights now
cd backend && python -m app.nlu.smoke --model-dir ../models/nlu   # no DB needed
NLU_ENABLED=true NLU_MODEL_DIR=models/nlu uvicorn app.main:app --app-dir backend --port 8001
# or the whole stack in Docker (CPU torch, ~3 GB RAM):
make up-nlu                 # chat UI: http://localhost:8000/static/chat.html
```

Settings: `NLU_ENABLED`, `NLU_MODEL_DIR`, `NLU_MIN_CONFIDENCE` (override the
calibrated threshold), `NLU_RULES_FALLBACK` (default `true`: hand utterances
the NLU cannot parse to the rule-based pipeline; `false` returns a clarifying
template instead). If the weights cannot be loaded the app logs it and keeps
working on the rule-based pipeline.
Docker: `docker-compose.nlu.yml` builds with `EXTRAS=[nlu]` and mounts
`./models/nlu` (writable: the joined weights are written there).
NLU dialogue state lives in process memory (one worker, or sticky sessions). The models are loaded at startup (~1 GB of weights; budget
~3 GB RAM). CPU latency per turn has not been measured yet.

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
(idempotent, four Russian clinics: Moscow, Saint Petersburg, Kazan, Novosibirsk).

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
