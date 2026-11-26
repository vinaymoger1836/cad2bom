# CAD-to-BoM developer commands. Run from the repository root.
# On Windows without make: use `winget install ezwinports.make`, WSL, or run the underlying
# commands directly (each target below is a one-liner; e2e is `uv run python scripts/e2e.py`).

COMPOSE      = docker compose --env-file .env -f infra/docker-compose.yml
COMPOSE_E2E  = $(COMPOSE) -f infra/docker-compose.e2e.yml
BAKE         = docker buildx bake -f infra/docker-bake.hcl

.PHONY: help env up down logs build build-multiarch lint e2e clean backup restore health

help:
	@echo "make env              create .env with random local secrets (git-ignored)"
	@echo "make up               build and start the stack (http://localhost:8080)"
	@echo "make down             stop the stack (keeps data)"
	@echo "make logs             follow container logs"
	@echo "make build            build the images for this machine"
	@echo "make build-multiarch  build linux/amd64 + linux/arm64 images (needs buildx + QEMU)"
	@echo "make lint             ruff, mypy, eslint, tsc"
	@echo "make e2e              full end-to-end check (brings the stack up, tears it down)"
	@echo "make clean            stop the stack and delete its volumes (database!)"
	@echo "make backup           database dump + job files + library store into backups/ (rotated)"
	@echo "make restore B=<dir>  restore a backup made by make backup (stops the stack first)"
	@echo "make health           check the running stack (exit 1 on failure; alerts if configured)"

env:
	uv run python scripts/init_env.py

up: env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

build: env
	$(COMPOSE) build

build-multiarch:
	$(BAKE)

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd packages/domain && uv run mypy src
	cd packages/rules && uv run mypy src
	cd packages/jobs && uv run mypy src
	cd packages/library && uv run mypy src
	cd packages/agents && uv run mypy src
	cd packages/exports && uv run mypy src
	cd tools/fixtures && uv run mypy src
	cd apps/api && uv run mypy src
	cd apps/worker && uv run mypy src
	cd apps/cad-worker && uv run mypy src
	cd apps/native-adapter && uv run mypy src
	pnpm gen:types
	pnpm --filter @c2b/web typecheck
	pnpm --filter @c2b/web lint

e2e:
	uv run python scripts/e2e.py

clean:
	$(COMPOSE) down -v

backup:
	sh scripts/backup.sh

restore:
	sh scripts/restore.sh $(B)

health:
	sh scripts/healthcheck.sh
