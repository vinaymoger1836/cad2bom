# syntax=docker/dockerfile:1.7
# Web build + Caddy front door in one image. Build context: the repository root.

# 1) Export JSON Schemas from the Pydantic models (the TS types are generated, never hand-edited).
FROM python:3.12-slim-bookworm AS schemas
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY apps/cad-worker ./apps/cad-worker
RUN uv sync --frozen --no-dev --no-editable --package c2b-domain \
    && /opt/venv/bin/python -m c2b_domain.export_schema /out

# 2) Generate the TS types and build the static site.
FROM node:22-slim AS web
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH CI=true
RUN npm install -g pnpm@11.18.0
WORKDIR /src
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY --from=schemas /out ./packages/ts-types
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile
RUN pnpm exec json2ts -i packages/ts-types/schema -o packages/ts-types/src
COPY apps/web ./apps/web
RUN pnpm --filter @c2b/web build

# 3) Caddy serves the build and forwards /api/* to the API.
FROM caddy:2.10-alpine
# The stock binary carries a file capability (bind to ports < 1024). We listen on 8080 and run
# with all capabilities dropped, where such a binary cannot even be executed, so remove it.
RUN apk add --no-cache libcap-utils && setcap -r /usr/bin/caddy && apk del libcap-utils
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=web /src/apps/web/dist /srv
EXPOSE 8080
