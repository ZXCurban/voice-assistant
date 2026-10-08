# Canonical checks (mirrored in AGENTS.md and .github/workflows/ci.yml):
#   ruff check backend | ruff format --check backend | mypy backend/app | pytest
.PHONY: install dev lint format typecheck test migrate revision up up-nlu nlu-weights down docker-build compose-config demo seed

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --app-dir backend

lint:
	ruff check backend
	ruff format --check backend

format:
	ruff format backend
	ruff check --fix backend

typecheck:
	mypy backend/app

test:
	pytest

migrate:
	alembic -c backend/alembic.ini upgrade head

revision:
	alembic -c backend/alembic.ini revision --autogenerate -m "$(m)"

up:
	docker compose up --build

up-nlu:
	docker compose -f docker-compose.yml -f docker-compose.nlu.yml up --build

nlu-weights:
	cd backend && python -m app.nlu.weights ../models/nlu

down:
	docker compose down

docker-build:
	docker build -t hackathon-skeleton:local .

compose-config:
	docker compose config --quiet

seed:
	cd backend && python -m app.db.seed_demo

demo:
	python scripts/demo.py
