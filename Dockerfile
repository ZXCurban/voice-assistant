FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

COPY pyproject.toml README.md ./
COPY backend/ ./backend/

# EXTRAS="[nlu]" adds torch/transformers for the local NLU (large image, see README).
ARG EXTRAS=""
ARG PIP_EXTRA_INDEX_URL=""
RUN pip install --upgrade pip && \
    if [ -n "$PIP_EXTRA_INDEX_URL" ]; then \
        pip install --extra-index-url "$PIP_EXTRA_INDEX_URL" ".${EXTRAS}"; \
    else \
        pip install ".${EXTRAS}"; \
    fi

WORKDIR /code/backend

EXPOSE 8000

# entrypoint.sh runs migrations/seeding only when explicitly enabled.
ENTRYPOINT ["sh", "entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
