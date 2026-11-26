"""The procrastinate app and the two queues (docs/02 §2, ADR-011).

The API defers tasks *by name* through `configure_task`, so it never imports worker code.
The worker registers the implementations with `@app.task(name=..., queue=...)`.
"""

import logging
from uuid import UUID

import procrastinate

from c2b_jobs.settings import get_settings

QUEUE_PIPELINE = "pipeline"
QUEUE_CAD = "cad"
QUEUE_NATIVE = "native"

TASK_RUN_PIPELINE = "pipeline.run_pipeline"
TASK_CAD_EXTRACT = "cad.extract"
TASK_NATIVE_CONVERT = "native.convert"

logging.getLogger("procrastinate").setLevel(logging.INFO)


def _make_app() -> procrastinate.App:
    # Created lazily-safe: PsycopgConnector does not connect until the app is opened.
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=get_settings().database_url),
    )


app = _make_app()


async def defer_run_pipeline(job_id: UUID) -> int:
    """Queue the pipeline for a job. One pending run per job (queueing_lock)."""
    return await app.configure_task(
        name=TASK_RUN_PIPELINE,
        queue=QUEUE_PIPELINE,
        queueing_lock=f"pipeline:{job_id}",
    ).defer_async(job_id=str(job_id))


async def defer_cad_extract(job_id: UUID, upload_id: UUID) -> int:
    return await app.configure_task(
        name=TASK_CAD_EXTRACT,
        queue=QUEUE_CAD,
        lock="cad-worker",  # one extraction at a time: the sandbox has 2 CPUs and 3 GB
    ).defer_async(job_id=str(job_id), upload_id=str(upload_id))


async def defer_native_convert(job_id: UUID, upload_id: UUID) -> int:
    """Queue a native conversion. One at a time: Autodesk's free tier is small and we poll it."""
    return await app.configure_task(
        name=TASK_NATIVE_CONVERT,
        queue=QUEUE_NATIVE,
        lock="aps",
    ).defer_async(job_id=str(job_id), upload_id=str(upload_id))
