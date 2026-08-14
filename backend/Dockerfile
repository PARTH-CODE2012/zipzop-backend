# Multi-stage. `dev` mounts the source and reloads; `prod` copies it in.
#
# FFmpeg is installed in both because the ingest and render workers shell out
# to it, and the same image serves the API and the workers — one image to
# build, one to keep patched.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a source change does not invalidate the layer.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]"

# ------------------------------------------------------------------ dev ----
FROM base AS dev
# Source arrives via a bind mount in docker-compose, so nothing is copied.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ----------------------------------------------------------------- prod ----
FROM base AS prod

COPY . .
RUN pip install --no-deps -e .

# Run as a non-root user. A container that does not need root should not have it.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
