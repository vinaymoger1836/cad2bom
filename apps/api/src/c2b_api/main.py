"""FastAPI application entrypoint."""

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request

from c2b_api.admin import router as admin_router
from c2b_api.errors import ProblemError, install_handlers
from c2b_api.jobs import router as jobs_router
from c2b_api.ratelimit import RateLimitMiddleware
from c2b_api.retention import sweep_loop
from c2b_jobs import repo
from c2b_jobs.migrate import run_migrations
from c2b_jobs.queue import app as queue_app
from c2b_jobs.settings import get_settings
from c2b_library.access import seed_store

log = logging.getLogger("c2b.api")

MIN_FREE_BYTES = 200 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    applied = await asyncio.to_thread(run_migrations, settings.database_url)
    if applied:
        log.info("migrations applied: %s", applied)
    # The image's library becomes version 1 of the store, so jobs can be pinned to it (ADR-019).
    seeded = await asyncio.to_thread(seed_store)
    log.info("library store ready, baseline %s", seeded)
    pool = repo.make_pool(settings.database_url)
    await pool.open(wait=True)
    app.state.pool = pool
    sweeper = asyncio.create_task(sweep_loop(pool), name="retention-sweep")
    async with queue_app.open_async():
        yield
    sweeper.cancel()
    with suppress(asyncio.CancelledError):
        await sweeper
    await pool.close()


_production = os.environ.get("C2B_ENV", "development") == "production"

app = FastAPI(
    title="CAD-to-BoM API",
    version="0.0.0",
    lifespan=lifespan,
    # The interactive docs are for development; a public deployment does not publish its API map.
    docs_url=None if _production else "/docs",
    openapi_url=None if _production else "/openapi.json",
    redoc_url=None,
)
install_handlers(app)
app.add_middleware(RateLimitMiddleware)
app.include_router(jobs_router)
app.include_router(admin_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@app.get("/readyz", tags=["meta"])
async def readyz(request: Request) -> dict[str, Any]:
    """Readiness: the database answers, the data volume has room, the library store is there."""
    s = get_settings()
    try:
        async with request.app.state.pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        raise ProblemError(
            503, "not_ready", "Not ready", "The database is not reachable yet."
        ) from exc
    try:
        free = shutil.disk_usage(s.data_dir).free
    except OSError as exc:
        raise ProblemError(
            503, "not_ready", "Not ready", "The data volume is not reachable."
        ) from exc
    if free < MIN_FREE_BYTES:
        raise ProblemError(
            503, "not_ready", "Not ready", "The data volume is almost full; free some space."
        )
    if not (s.library_store_dir / "active").is_file():
        raise ProblemError(503, "not_ready", "Not ready", "The library store is not ready yet.")
    return {"status": "ready", "data_free_mb": free // (1024 * 1024)}
