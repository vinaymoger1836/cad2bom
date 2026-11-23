# syntax=docker/dockerfile:1.7
# Build context: the repository root (see infra/docker-compose.yml and infra/docker-bake.hcl).

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Every workspace member must be present for `uv sync --frozen`; the .dockerignore keeps it small.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY tools ./tools
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY apps/cad-worker ./apps/cad-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package c2b-api

FROM python:3.12-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# The knowledge library is data, read at run time (C2B_LIBRARY_DIR, default ./library).
COPY library /app/library
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "c2b_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
