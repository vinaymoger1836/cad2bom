"""Native CAD conversion task (ADR-018): SolidWorks and Fusion files through Autodesk.

The `native` queue runs one conversion at a time (`lock="aps"`). After a file is converted the
job continues on the normal path: a converted STEP goes to the cad-worker like any STEP; a
SolidWorks file (no STEP from Autodesk) is read from its OBJ meshes, with the object properties
merged into the nodes by `tasks._handle_cad_result`.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import UUID, uuid4

from c2b_jobs import repo
from c2b_jobs.queue import (
    QUEUE_NATIVE,
    TASK_NATIVE_CONVERT,
    app,
    defer_cad_extract,
    defer_native_convert,
)
from c2b_jobs.settings import Settings, get_settings
from c2b_native_adapter import (
    ApsAdapter,
    NativeAdapter,
    RecordTransport,
    ReplayTransport,
    StubNativeAdapter,
    UsageGate,
)
from c2b_worker import cad_client
from c2b_worker.context import pool
from c2b_worker.tasks import _build_bom_for_job, _fail, _handle_cad_result

log = logging.getLogger("c2b.worker.native")


async def _no_sleep(_: float) -> None:
    return None


def make_adapter(settings: Settings) -> NativeAdapter:
    """The converter for this server: Autodesk live, recorded or replayed, or the stub."""
    if not settings.aps_configured:
        return StubNativeAdapter()
    if settings.aps_mode == "replay":
        return ApsAdapter(
            settings.aps_client_id or "fixture-client",
            settings.aps_client_secret or "fixture-secret",
            transport=ReplayTransport(settings.aps_fixture_dir),
            timeout_s=settings.aps_timeout_s,
            sleep=_no_sleep,
        )
    transport = RecordTransport(settings.aps_fixture_dir) if settings.aps_mode == "record" else None
    return ApsAdapter(
        settings.aps_client_id,
        settings.aps_client_secret,
        transport=transport,
        timeout_s=settings.aps_timeout_s,
    )


def _file_facts(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return path.stat().st_size, h.hexdigest()


@app.task(name=TASK_NATIVE_CONVERT, queue=QUEUE_NATIVE)
async def native_convert(job_id: str, upload_id: str) -> None:
    try:
        await _native_convert_inner(job_id, upload_id)
    except Exception:
        log.exception("native conversion failed for job %s", job_id)
        await _fail(
            UUID(job_id),
            "Something went wrong while converting your CAD file. Your file is safe; "
            "please try again, or export it as STEP and upload that.",
        )


async def _native_convert_inner(job_id: str, upload_id: str) -> None:
    jid, uid = UUID(job_id), UUID(upload_id)
    db = pool()
    settings = get_settings()
    job = await repo.get_job(db, jid)
    upload = await repo.get_upload(db, jid, uid)
    if job is None or upload is None:
        await _fail(jid, "The uploaded CAD file could not be found.")
        return
    if not job.get("consent_native_conversion"):
        # The API refuses this earlier; this is the second lock, because files leave the server.
        await _fail(
            jid,
            "Converting this file needs your consent to send it to Autodesk. "
            "Start a new BoM and confirm it, or export your model as STEP.",
        )
        return

    src = settings.data_dir / str(upload["stored_path"])
    fmt = str(upload["detected_type"])
    await repo.add_event(
        db,
        jid,
        "step",
        f"Sending {upload['filename']} to Autodesk for conversion (you agreed to this)",
        {"native": fmt},
    )
    adapter = make_adapter(settings)
    gate = UsageGate(
        reserve=lambda n: repo.aps_reserve(db, n, settings.aps_monthly_cap),
        refund=lambda n: repo.aps_refund(db, n),
    )
    result = await adapter.convert(src, settings.converted_dir / job_id, gate)

    if result.status != "ok":
        await repo.save_job_native(
            db,
            jid,
            uid,
            source_format=fmt,
            status=result.status,
            message=result.message,
            usage_jobs=result.usage_jobs,
            properties=[],
            obj_relpaths=[],
        )
        await repo.add_event(
            db,
            jid,
            "notice",
            result.message,
            {"code": f"native_{result.status}", "severity": "warning"},
        )
        await _continue_after_native(jid, failure=result.message)
        return

    await repo.save_job_native(
        db,
        jid,
        uid,
        source_format=fmt,
        status="ok",
        message="",
        usage_jobs=result.usage_jobs,
        properties=[{"name": n.name, "path": n.path, "props": n.props} for n in result.properties],
        obj_relpaths=[p.relative_to(settings.data_dir).as_posix() for p in result.obj_paths],
    )
    if result.step_path is not None:
        size, sha = _file_facts(result.step_path)
        await repo.add_upload(
            db,
            upload_id=uuid4(),
            job_id=jid,
            filename=f"{Path(str(upload['filename'])).stem}.step (converted)",
            kind="cad",
            detected_type="step",
            size_bytes=size,
            sha256=sha,
            stored_path=result.step_path.relative_to(settings.data_dir).as_posix(),
            origin="converted",
            derived_from=uid,
        )
    for note in result.notes:
        await repo.add_event(db, jid, "notice", note, {"code": "native_note", "severity": "info"})
    await repo.add_event(
        db,
        jid,
        "native_converted",
        f"Autodesk converted {upload['filename']} ({result.usage_jobs} translation job(s) used)",
        {"usage_jobs": result.usage_jobs, "source_format": fmt},
    )
    await _continue_after_native(jid)


async def _continue_after_native(jid: UUID, failure: str | None = None) -> None:
    """Pick the next step once one native file has been dealt with."""
    db = pool()
    settings = get_settings()
    more = await repo.first_native_upload(db, jid)
    if more is not None:
        await defer_native_convert(jid, more)
        return

    await repo.set_status(db, jid, "extracting")
    await repo.add_event(db, jid, "status", "Reading your files", {"status": "extracting"})
    step = await repo.first_cad_upload(db, jid)  # a converted STEP, else a STEP the user added
    if step is not None:
        await defer_cad_extract(jid, step)
        return

    # SolidWorks: Autodesk offers no STEP, so read the meshes it returned.
    for row in await repo.list_job_native(db, jid):
        if row["status"] == "ok" and row["obj_relpaths"]:
            upload = await repo.get_upload(db, jid, row["upload_id"])
            name = Path(str(upload["filename"] if upload else "model")).stem
            paths = [(settings.data_dir / r).as_posix() for r in row["obj_relpaths"]]
            try:
                mesh = await cad_client.mesh_extract(
                    settings.cad_socket, paths, name, settings.cad_timeout_s
                )
            except cad_client.CadWorkerError as exc:
                await _fail(jid, str(exc))
                return
            await _handle_cad_result(db, jid, UUID(str(row["upload_id"])), mesh)
            return

    uploads = await repo.list_uploads(db, jid)
    if any(u.kind != "cad" for u in uploads):
        await repo.add_event(
            db,
            jid,
            "notice",
            (failure or "The CAD file could not be converted.")
            + " Continuing with your other files; add a STEP export to include the CAD data.",
        )
        await _build_bom_for_job(db, jid, str(jid), extract_obj=None)
        return
    await _fail(
        jid,
        failure or "The CAD file could not be converted. Please export it as STEP and upload that.",
    )
