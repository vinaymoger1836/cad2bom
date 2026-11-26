"""Worker entrypoint: one process serving the `pipeline`, `cad` and `native` queues."""

import asyncio
import logging
import socket

from c2b_jobs import repo
from c2b_jobs.migrate import run_migrations
from c2b_jobs.queue import QUEUE_CAD, QUEUE_NATIVE, QUEUE_PIPELINE, app
from c2b_jobs.settings import get_settings
from c2b_worker import native_tasks, tasks  # noqa: F401  (registers the tasks)
from c2b_worker.context import set_pool


async def _run() -> None:
    settings = get_settings()
    # Idempotent and lock-protected, so it is safe even though the API normally does this first.
    await asyncio.to_thread(run_migrations, settings.database_url)
    pool = repo.make_pool(settings.database_url)
    await pool.open(wait=True)
    set_pool(pool)
    async with app.open_async():
        await app.run_worker_async(
            queues=[QUEUE_PIPELINE, QUEUE_CAD, QUEUE_NATIVE],
            name=f"worker-{socket.gethostname()}",
            concurrency=4,
            wait=True,
            # Heartbeats let another worker pick up jobs from one that died (stalled recovery).
            update_heartbeat_interval=10,
            stalled_worker_timeout=40,
            shutdown_graceful_timeout=20,
        )
    await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
