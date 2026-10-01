# Docker (development skeleton)

`Dockerfile` and `docker-compose.yml` live at the repo root on purpose;
this directory is reserved for future assets (init scripts, configs).

- Local dev: `docker compose up --build` (Postgres 16 + Redis 7 + API with `--reload`).
- Validate without running: `make compose-config`, `make docker-build`.
- CI runs `docker build -t hackathon-skeleton:ci .` only (no registry push).
- `.dockerignore` keeps the build context small (excludes git, caches, docs, infra).

No production deployment architecture yet; keep it simple until needed.
