FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

COPY pyproject.toml README.md ./
COPY backend/ ./backend/

# EXTRAS="[nlu]" adds torch/transformers for the local NLU (large image, see README).
ARG EXTRAS=""
RUN pip install --upgrade pip && pip install ".${EXTRAS}"

WORKDIR /code/backend

EXPOSE 8000

# Migrate + seed on every start (both idempotent), then run the server.
# docker-compose.yml overrides CMD with --reload for local development.
ENTRYPOINT ["sh", "entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
