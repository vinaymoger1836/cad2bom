"""Procrastinate tasks for the two queues: `pipeline` and `cad`.

P0 behaviour: run_pipeline moves the job to `extracting` and asks the cad-worker for a stub
extraction. Later phases add the real pipeline steps (docs/02 §3) here.
"""

import logging
from uuid import UUID

from c2b_jobs import repo
from c2b_jobs.queue import (
    QUEUE_CAD,
    QUEUE_PIPELINE,
    TASK_CAD_EXTRACT,
    TASK_RUN_PIPELINE,
    app,
    defer_cad_extract,
)
from c2b_jobs.settings import get_settings
from c2b_worker import cad_client
from c2b_worker.context import pool

log = logging.getLogger("c2b.worker")


async def _fail(jid: UUID, message: str, **data: object) -> None:
    """Stop the job with a message for the person, as both a notice and a status change."""
    db = pool()
    await repo.set_status(db, jid, "failed")
    await repo.add_event(db, jid, "notice", message, dict(data))
    await repo.add_event(db, jid, "status", "Stopped", {"status": "failed"})


@app.task(name=TASK_RUN_PIPELINE, queue=QUEUE_PIPELINE)
async def run_pipeline(job_id: str) -> None:
    jid = UUID(job_id)
    db = pool()
    await repo.set_status(db, jid, "extracting")
    await repo.add_event(db, jid, "status", "Reading your files", {"status": "extracting"})
    cad_upload = await repo.first_cad_upload(db, jid)
    if cad_upload is None:
        await repo.add_event(
            db, jid, "notice", "No CAD file was uploaded, so there is no 3D structure to read."
        )
        return
    await defer_cad_extract(jid, cad_upload)


@app.task(name=TASK_CAD_EXTRACT, queue=QUEUE_CAD)
async def cad_extract(job_id: str, upload_id: str) -> None:
    jid, uid = UUID(job_id), UUID(upload_id)
    db = pool()
    settings = get_settings()
    rel = await repo.get_upload_path(db, jid, uid)
    if rel is None:
        await _fail(jid, "The uploaded CAD file could not be found.")
        return
    path = (settings.data_dir / rel).as_posix()
    try:
        result = await cad_client.extract(settings.cad_socket, path, settings.cad_timeout_s)
    except cad_client.CadWorkerError as exc:
        log.warning("cad-worker unavailable for job %s: %s", job_id, exc)
        await _fail(jid, str(exc))
        return
    if result.get("status") == "stub":
        await repo.add_event(
            db, jid, "cad_stub", "CAD reader answered (placeholder result in this version)", result
        )
        return
    message = str(result.get("message") or "The CAD reader could not process this file.")
    await _fail(jid, message, cad_status=result.get("status"))
