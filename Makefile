.PHONY: install dev lint format typecheck test migrate revision up down

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

down:
	docker compose down
