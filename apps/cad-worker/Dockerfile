# syntax=docker/dockerfile:1.7
# Sandboxed OpenCascade worker. Runs with no network, no database credentials, a read-only
# root filesystem and CPU/RAM/time limits (infra/docker-compose.yml, ADR-012).
# Build context: the repository root.

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY apps/cad-worker ./apps/cad-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package c2b-cad-worker

FROM python:3.12-slim-bookworm
# Shared libraries the OpenCascade/VTK wheels link against (software GL only; no display).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libxrender1 libxext6 libx11-6 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp \
    MPLCONFIGDIR=/tmp
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import os, stat; assert stat.S_ISSOCK(os.stat('/run/c2b/cad.sock').st_mode)"]
CMD ["c2b-cad-worker"]
