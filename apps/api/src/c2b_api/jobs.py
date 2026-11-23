"""Job routes: create, read, upload, start, events."""

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import FileResponse

from c2b_api import sse, uploads
from c2b_api.auth import JobRow, hash_token, new_token
from c2b_api.errors import ProblemError
from c2b_api.magic import ACCEPTED_EXTENSIONS
from c2b_domain import JobCreate, JobCreated, JobOut, JobUpdate, ProductInfo, UploadOut
from c2b_jobs import queue, repo
from c2b_jobs.repo import Pool
from c2b_jobs.settings import get_settings

router = APIRouter()


def _pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


@router.get("/config", tags=["meta"])
async def config() -> dict[str, Any]:
    """Limits and accepted types, so the UI can pre-check (the server stays authoritative)."""
    s = get_settings()
    return {
        "max_cad_mb": s.max_cad_mb,
        "max_other_mb": s.max_other_mb,
        "max_files_per_job": s.max_files_per_job,
        "accepted_extensions": ACCEPTED_EXTENSIONS,
        "cad_extensions": [".step", ".stp"],
    }


@router.post("/jobs", status_code=201, tags=["jobs"])
async def create_job(body: JobCreate, request: Request) -> JobCreated:
    job_id = uuid4()
    token = new_token()
    product = ProductInfo.model_validate(body.model_dump())
    await repo.create_job(
        _pool(request),
        job_id,
        product,
        hash_token(token),
        get_settings().library_version,
        consent_native_conversion=body.consent_native_conversion,
    )
    await repo.add_event(_pool(request), job_id, "status", "Job created", {"status": "uploading"})
    # The token sits in the URL fragment: it is never sent to servers or written to access logs.
    return JobCreated(id=job_id, link=f"/j/{job_id}#{token}", token=token)


async def _job_out(pool: Pool, row: dict[str, object]) -> JobOut:
    job_id = UUID(str(row["id"]))
    return JobOut(
        id=job_id,
        status=row["status"],  # type: ignore[arg-type]
        product=ProductInfo.model_validate(row["product"]),
        library_version=str(row["library_version"]),
        consent_native_conversion=bool(row.get("consent_native_conversion", False)),
        created_at=row["created_at"],  # type: ignore[arg-type]
        uploads=await repo.list_uploads(pool, job_id),
    )


@router.get("/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: UUID, job: JobRow, request: Request) -> JobOut:
    return await _job_out(_pool(request), job)


@router.patch("/jobs/{job_id}", tags=["jobs"])
async def update_job(
    job_id: UUID,
    job: JobRow,
    body: JobUpdate,
    request: Request,
) -> JobOut:
    pool = _pool(request)
    if body.consent_native_conversion is not None:
        await repo.set_consent_native_conversion(pool, job_id, body.consent_native_conversion)
    row = await repo.get_job(pool, job_id)
    assert row is not None
    return await _job_out(pool, row)


@router.get("/jobs/{job_id}/model.glb", tags=["jobs"])
async def get_model_glb(
    job_id: UUID,
    job: JobRow,
) -> FileResponse:
    s = get_settings()
    glb_path = s.data_dir / "jobs" / str(job_id) / "out" / "model.glb"
    if not glb_path.exists():
        raise ProblemError(
            404,
            "not_found",
            "Model not found",
            "No 3D GLB model is available for this job yet.",
        )
    return FileResponse(
        path=glb_path,
        media_type="model/gltf-binary",
        filename="model.glb",
    )


@router.get("/jobs/{job_id}/files", tags=["uploads"])
async def list_files(job_id: UUID, job: JobRow, request: Request) -> list[UploadOut]:
    return await repo.list_uploads(_pool(request), job_id)


@router.post("/jobs/{job_id}/files", status_code=201, tags=["uploads"])
async def upload_file(
    job_id: UUID,
    job: JobRow,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=512)],
    content_length: Annotated[int | None, Header()] = None,
) -> UploadOut:
    """Raw-body upload: send the file bytes as the request body, name in `?filename=`."""
    pool = _pool(request)
    s = get_settings()
    if job["status"] != "uploading":
        raise ProblemError(
            422,
            "wrong_state",
            "This job is already being built",
            "Files can only be added before the build starts.",
        )
    if await repo.count_uploads(pool, job_id) >= s.max_files_per_job:
        raise ProblemError(
            422,
            "too_many_files",
            "Too many files",
            f"A job can hold at most {s.max_files_per_job} files.",
        )
    upload_id = uuid4()
    stored = await uploads.receive(
        request.stream(),
        filename=filename,
        content_length=content_length,
        dest_dir=s.data_dir / "jobs" / str(job_id) / "in",
        file_stem=str(upload_id),
        max_cad=s.max_cad_bytes,
        max_other=s.max_other_bytes,
    )
    rel = stored.path.relative_to(s.data_dir).as_posix()
    try:
        out = await repo.add_upload(
            pool,
            upload_id=upload_id,
            job_id=job_id,
            filename=stored.display_name,
            kind=stored.spec.kind,
            detected_type=stored.spec.detected,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            stored_path=rel,
        )
    except BaseException:
        stored.path.unlink(missing_ok=True)
        raise
    await repo.add_event(
        pool,
        job_id,
        "upload",
        f"Received {stored.display_name}",
        {"upload_id": str(upload_id), "kind": stored.spec.kind, "size_bytes": stored.size_bytes},
    )
    return out


@router.post("/jobs/{job_id}/start", status_code=202, tags=["jobs"])
async def start_job(job_id: UUID, job: JobRow, request: Request) -> JobOut:
    pool = _pool(request)
    if job["status"] != "uploading":
        raise ProblemError(
            422, "wrong_state", "Already started", "This job has already been started."
        )
    if await repo.count_uploads(pool, job_id) == 0:
        raise ProblemError(
            422,
            "wrong_state",
            "Nothing to build from",
            "Upload at least one file (a STEP file is best) before starting.",
        )
    # Claim the transition atomically so two quick clicks cannot start the job twice.
    if not await repo.claim_start(pool, job_id):
        raise ProblemError(
            422, "wrong_state", "Already started", "This job has already been started."
        )
    await repo.add_event(pool, job_id, "step", "Queued for processing")
    await queue.defer_run_pipeline(job_id)
    row = await repo.get_job(pool, job_id)
    assert row is not None
    return await _job_out(pool, row)


@router.get("/jobs/{job_id}/events", tags=["jobs"])
async def job_events(
    job_id: UUID,
    job: JobRow,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: int = 0,
) -> Response:
    """SSE stream of job events. Resume with `Last-Event-ID` (or `?after=`)."""
    start = after
    if last_event_id and last_event_id.isdigit():
        start = max(start, int(last_event_id))
    return sse.job_stream_response(request, _pool(request), job_id, start)


@router.get("/demo/stream", tags=["meta"])
async def demo_stream(request: Request) -> Response:
    """Public demo of the progress stream; carries no job data and ends after ten ticks."""
    return sse.demo_response(request)
