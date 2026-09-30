# Hackathon Skeleton

Production-ready Python skeleton **without business logic**.

## Stack

- Python 3.13+, FastAPI, PostgreSQL (SQLAlchemy 2.x + asyncpg), Alembic, Redis
- pytest, Ruff, mypy (strict), Docker / Docker Compose, GitHub Actions

## Structure

```text
backend/
  app/
    api/          # HTTP layer (routers); v1 aggregated in api/v1
    core/         # config, logging
    db/           # async engine/session + Redis client holders
    models/       # ORM base only (no domain models)
    schemas/      # Pydantic schemas (health only)
    repositories/ # persistence layer (empty placeholder)
    services/     # use-cases (empty placeholder)
    main.py       # create_app() + lifespan
  tests/          # smoke tests
  alembic/        # migrations (env.py + script.py.mako)
  alembic.ini
infra/docker/     # reserved for extra Docker assets
docs/
.github/workflows/ci.yml  # Ruff → mypy → pytest
```

Layering rule: `api → services → repositories → models/db`. No imports upward.

## Quickstart

```bash
cp .env.example .env
docker compose up --build
# API: http://localhost:8000/health
```

Local run without Docker:

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --app-dir backend
pytest
```

## Checks

```bash
ruff check backend && ruff format --check backend
mypy backend/app
pytest
```

Or via Make: `make lint`, `make typecheck`, `make test`.

## Migrations

```bash
alembic -c backend/alembic.ini revision --autogenerate -m "init"
alembic -c backend/alembic.ini upgrade head
```

## Notes

- `GET /health` is the only endpoint and does not touch DB/Redis.
- Engine/Redis clients are lazy singletons: object creation does no I/O, safe in tests/CI.
