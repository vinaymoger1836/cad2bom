"""FastAPI application entrypoint."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from c2b_api.errors import ProblemError, install_handlers
from c2b_api.jobs import router as jobs_router
from c2b_jobs import repo
from c2b_jobs.migrate import run_migrations
from c2b_jobs.queue import app as queue_app
from c2b_jobs.settings import get_settings

log = logging.getLogger("c2b.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    applied = await asyncio.to_thread(run_migrations, settings.database_url)
    if applied:
        log.info("migrations applied: %s", applied)
    pool = repo.make_pool(settings.database_url)
    await pool.open(wait=True)
    app.state.pool = pool
    async with queue_app.open_async():
        yield
    await pool.close()


app = FastAPI(
    title="CAD-to-BoM API",
    version="0.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)
install_handlers(app)
app.include_router(jobs_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@app.get("/readyz", tags=["meta"])
async def readyz(request: Request) -> dict[str, str]:
    """Readiness: the database answers."""
    try:
        async with request.app.state.pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        raise ProblemError(
            503, "not_ready", "Not ready", "The database is not reachable yet."
        ) from exc
    return {"status": "ready"}
