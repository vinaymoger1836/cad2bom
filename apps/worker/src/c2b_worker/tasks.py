"""Procrastinate task handlers (docs/02 §3, docs/03 §3).

Runs in the `worker` container. Dispatches CAD extraction to the sandboxed
`cad-worker` over a UNIX domain socket.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from c2b_domain.cad import CadExtractResult
from c2b_jobs import mapping_store, repo
from c2b_jobs.queue import (
    QUEUE_CAD,
    QUEUE_PIPELINE,
    TASK_CAD_EXTRACT,
    TASK_RUN_PIPELINE,
    app,
    defer_cad_extract,
)
from c2b_jobs.repo import Pool
from c2b_jobs.settings import get_settings
from c2b_library.loader import load_library
from c2b_rules.engine import build_ebom
from c2b_worker import cad_client
from c2b_worker.context import pool

log = logging.getLogger("c2b.worker")


async def _fail(
    jid: UUID,
    message: str,
    notices: list[dict[str, Any]] | None = None,
) -> None:
    db = pool()
    await repo.set_status(db, jid, "failed")
    data: dict[str, Any] = {"error": message}
    if notices:
        data["notices"] = notices
    await repo.add_event(db, jid, "notice", message, data)
    await repo.add_event(db, jid, "status", "Stopped", {"status": "failed"})


async def _build_bom_for_job_inner(
    db: Pool,
    jid: UUID,
    job_id: str,
    extract_obj: CadExtractResult | None,
) -> None:
    """Build draft EBOM from CAD extract and other uploads (BoM files, datasheets, photos)."""
    settings = get_settings()
    library, _ = load_library(settings.library_dir)
    if library is None:
        log.error("Failed to load library for job %s", job_id)
        await _fail(jid, "Internal error: could not load library definitions")
        return

    # Discover non-CAD uploads
    uploads = await repo.list_uploads(db, jid)
    bom_file_path: Path | None = None
    datasheet_paths: list[Path | str] = []
    photo_paths: list[Path | str] = []

    for u in uploads:
        rel = await repo.get_upload_path(db, jid, u.id)
        if not rel:
            continue
        p = settings.data_dir / rel
        ext = p.suffix.lower()
        if ext in [".xlsx", ".xls", ".csv"] and bom_file_path is None:
            bom_file_path = p
        elif ext == ".pdf":
            datasheet_paths.append(p)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            photo_paths.append(p)

    # Checkpoint step 4
    await repo.add_event(db, jid, "step", "Step 4: Classification and recognition")
    # Checkpoint step 5
    await repo.add_event(db, jid, "step", "Step 5: Materials and mass")
    # Checkpoint step 6
    await repo.add_event(db, jid, "step", "Step 6: EBOM Structure")

    # Step 7: Merge if other inputs present
    if bom_file_path or datasheet_paths or photo_paths:
        await repo.add_event(db, jid, "step", "Step 7: Merge with user BoM, photos, datasheets")

    # What the person typed about the product (description, annual volume) feeds the rules too.
    product_desc = ""
    annual_volume: int | None = None
    job_row = await repo.get_job(db, jid)
    product = job_row.get("product") if job_row else None
    if isinstance(product, dict):
        product_desc = str(product.get("description") or "")
        vol = product.get("annual_volume")
        annual_volume = int(vol) if isinstance(vol, int) else None

    bom_draft = build_ebom(
        extract=extract_obj,
        library=library,
        job_id=job_id,
        bom_file_path=bom_file_path,
        mapping_overrides=mapping_store.load_overrides(settings.data_dir, jid),
        datasheet_paths=datasheet_paths if datasheet_paths else None,
        photo_paths=photo_paths if photo_paths else None,
        photo_opt_in=True,
        product_desc=product_desc,
        annual_volume=annual_volume,
        assign_numbers=True,
    )

    # Step 4 fallback ladder & agent summary (docs/02 §3, docs/04 §9)
    from c2b_agents.pipeline import resolve_draft_with_ladder
    from c2b_agents.router import LlmRouter

    router = LlmRouter()
    bom_draft = await resolve_draft_with_ladder(
        draft=bom_draft,
        library=library,
        router=router,
        extract=extract_obj,
        product_desc=product_desc,
        run_l4_gap=True,
    )

    # Persist audited LLM calls to database
    for call in router.call_logs:
        await repo.record_llm_call(
            pool=db,
            job_id=jid,
            role=call.role,
            model=call.model,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            total_tokens=call.total_tokens,
            latency_ms=call.latency_ms,
            fallback_reason=call.fallback_reason,
            line_id=call.line_id,
            cassette_key=call.cassette_key,
        )

    # Checkpoint step 11
    await repo.add_event(db, jid, "step", "Step 11: Validation and confidence")

    # Persist draft BoM
    await repo.save_bom(db, jid, bom_draft.model_dump(by_alias=True, mode="json"))

    # Move status to review
    await repo.set_status(db, jid, "review")
    await repo.add_event(db, jid, "status", "Ready for review", {"status": "review"})


async def _build_bom_for_job(
    db: Pool,
    jid: UUID,
    job_id: str,
    extract_obj: CadExtractResult | None,
) -> None:
    """Build the draft; any unexpected error stops the job with a message instead of hanging."""
    try:
        await _build_bom_for_job_inner(db, jid, job_id, extract_obj)
    except Exception:
        log.exception("BoM build failed for job %s", job_id)
        await _fail(
            jid,
            "Something went wrong while building the BoM. Your files are safe; "
            "please try again, and report this if it keeps happening.",
        )


@app.task(name=TASK_RUN_PIPELINE, queue=QUEUE_PIPELINE)
async def run_pipeline(job_id: str) -> None:
    jid = UUID(job_id)
    db = pool()
    await repo.set_status(db, jid, "extracting")
    await repo.add_event(db, jid, "status", "Reading your files", {"status": "extracting"})
    cad_upload = await repo.first_cad_upload(db, jid)
    if cad_upload is None:
        # Check if there are other files (e.g. BoM file in B4)
        uploads = await repo.list_uploads(db, jid)
        if uploads:
            await repo.add_event(
                db,
                jid,
                "notice",
                "No CAD file was uploaded; processing tabular and document inputs directly.",
            )
            await _build_bom_for_job(db, jid, job_id, extract_obj=None)
            return

        await repo.add_event(db, jid, "notice", "No files were uploaded to build from.")
        await _fail(jid, "No files were uploaded to build from.")
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

    # Build BoM
    extract_obj = CadExtractResult.model_validate(result)
    await _build_bom_for_job(db, jid, job_id, extract_obj=extract_obj)
