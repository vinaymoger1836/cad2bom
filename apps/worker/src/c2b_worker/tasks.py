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
    if result.get("status") == "error":
        notices = result.get("notices") or []
        first_msg = (
            notices[0].get("message")
            if notices
            else str(result.get("message") or "The CAD reader could not process this file.")
        )
        await _fail(jid, first_msg, notices=notices)
        return

    # Real CAD extraction result
    leaf_count = int(result.get("leaf_count") or len(result.get("nodes") or []))
    await repo.save_cad_extract(db, jid, uid, result, leaf_count=leaf_count)

    for notice in result.get("notices") or []:
        await repo.add_event(db, jid, "notice", notice.get("message", ""), notice)

    node_count = len(result.get("nodes") or [])
    shape_count = len(result.get("shapes") or [])
    await repo.add_event(
        db,
        jid,
        "cad_extracted",
        f"Extracted {node_count} nodes ({leaf_count} leaves, {shape_count} unique shapes)",
        {
            "leaf_count": leaf_count,
            "node_count": node_count,
            "shape_count": shape_count,
            "glb_relpath": result.get("glb_relpath"),
        },
    )
    # Move status to building
    await repo.set_status(db, jid, "building")
    await repo.add_event(db, jid, "status", "Building BoM", {"status": "building"})

    # Phase 3 rules engine execution: steps 4-6 and 11
    from pathlib import Path

    from c2b_domain.cad import CadExtractResult
    from c2b_library.loader import load_library
    from c2b_rules.engine import build_ebom

    library, _ = load_library(Path("library"))
    if library is None:
        log.error("Failed to load library for job %s", job_id)
        await _fail(jid, "Internal error: could not load library definitions")
        return

    # Checkpoint step 4
    await repo.add_event(db, jid, "step", "Step 4: Classification and recognition")
    # Checkpoint step 5
    await repo.add_event(db, jid, "step", "Step 5: Materials and mass")
    # Checkpoint step 6
    await repo.add_event(db, jid, "step", "Step 6: EBOM Structure")

    extract_obj = CadExtractResult.model_validate(result)
    bom_draft = build_ebom(extract_obj, library, job_id=job_id)

    # Checkpoint step 11
    await repo.add_event(db, jid, "step", "Step 11: Validation and confidence")

    # Persist draft BoM
    await repo.save_bom(db, jid, bom_draft.model_dump(by_alias=True))

    # Move status to review
    await repo.set_status(db, jid, "review")
    await repo.add_event(db, jid, "status", "Ready for review", {"status": "review"})
