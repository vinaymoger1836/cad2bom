"""Job routes: create, read, upload, start, events."""

import functools
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from c2b_api import sse, uploads
from c2b_api.auth import JobRow, hash_token, new_token
from c2b_api.errors import ProblemError
from c2b_api.magic import ACCEPTED_EXTENSIONS
from c2b_domain import (
    BomDraft,
    ChatRequest,
    ChatResponse,
    JobCreate,
    JobCreated,
    JobOut,
    JobUpdate,
    ProductInfo,
    TableMapping,
    UploadOut,
)
from c2b_jobs import mapping_store, queue, repo
from c2b_jobs.repo import Pool
from c2b_jobs.settings import get_settings

router = APIRouter()


def _pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


async def _reopen_if_approved(pool: Pool, job_id: UUID, job: dict[str, object]) -> None:
    """A change after approval starts a new draft: the frozen versions stay as they are."""
    if job.get("status") == "approved":
        await repo.set_status(pool, job_id, "review")
        await repo.add_event(
            pool,
            job_id,
            "status",
            "Changed after approval: new draft",
            {"status": "review"},
        )


@functools.cache
def _confidence_bands() -> dict[str, float]:
    """High / medium confidence cut-offs from the library, so the UI colours match the rules."""
    from c2b_library.loader import load_library

    library, _ = load_library(get_settings().library_dir)
    if library and library.confidence:
        return {k: float(v) for k, v in library.confidence.bands.items()}
    return {"high": 0.85, "medium": 0.6}


@router.get("/config", tags=["meta"])
async def config() -> dict[str, Any]:
    """Limits and accepted types, so the UI can pre-check (the server stays authoritative)."""
    s = get_settings()
    return {
        "confidence_bands": _confidence_bands(),
        "max_cad_mb": s.max_cad_mb,
        "max_other_mb": s.max_other_mb,
        "max_files_per_job": s.max_files_per_job,
        "accepted_extensions": ACCEPTED_EXTENSIONS,
        "cad_extensions": [".step", ".stp"],
        "cad_timeout_s": s.cad_timeout_s,
    }


@functools.cache
def _library_version() -> str:
    """The library version a new job is pinned to (first `version:` line of VERSION.yaml)."""
    path = get_settings().library_dir / "VERSION.yaml"
    try:
        m = re.search(r'^version:\s*"?([^"\s#]+)', path.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        m = None
    return m.group(1) if m else get_settings().library_version


@router.post("/jobs", status_code=201, tags=["jobs"])
async def create_job(body: JobCreate, request: Request) -> JobCreated:
    job_id = uuid4()
    token = new_token()
    product = ProductInfo.model_validate(body.model_dump(exclude={"consent_native_conversion"}))
    await repo.create_job(
        _pool(request),
        job_id,
        product,
        hash_token(token),
        _library_version(),
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


async def _fail_if_stalled(pool: Pool, job_id: UUID, row: dict[str, object]) -> dict[str, object]:
    """Watchdog: a job that reports nothing for longer than the CAD limit plus 5 minutes is
    stopped with a reason, so nobody waits on a build that will never finish (killed worker,
    lost task). Runs whenever the job is read; the state change is idempotent."""
    if row["status"] not in {"extracting", "building"}:
        return row
    idle = await repo.seconds_since_last_event(pool, job_id)
    limit = get_settings().cad_timeout_s + 300
    if idle is None or idle < limit:
        return row
    message = (
        f"The build stopped responding (no progress for {int(idle // 60)} minutes). "
        "Your files are safe. Please start a new BoM; if it happens again with the same file, "
        "try a smaller or simpler export."
    )
    await repo.set_status(pool, job_id, "failed")
    await repo.add_event(pool, job_id, "notice", message, {"error": message, "stalled": True})
    await repo.add_event(pool, job_id, "status", "Stopped", {"status": "failed"})
    return {**row, "status": "failed"}


@router.get("/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: UUID, job: JobRow, request: Request) -> JobOut:
    pool = _pool(request)
    return await _job_out(pool, await _fail_if_stalled(pool, job_id, job))


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
    glb_path = s.data_dir / "out" / str(job_id) / "model.glb"
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


@router.get("/jobs/{job_id}/bom", tags=["jobs"])
async def get_job_bom(
    job_id: UUID,
    job: JobRow,
    request: Request,
) -> BomDraft:
    bom_data = await repo.get_bom(_pool(request), job_id)
    if bom_data is None:
        raise ProblemError(
            404,
            "not_found",
            "BoM not found",
            "No BoM has been generated for this job yet.",
        )
    return BomDraft.model_validate(bom_data)


@router.get("/jobs/{job_id}/excluded", tags=["jobs"])
async def get_excluded_nodes(
    job_id: UUID,
    job: JobRow,
    request: Request,
) -> list[dict[str, Any]]:
    """CAD nodes the rules left out of the BoM (reference geometry, hidden bodies) and why."""
    extract = await repo.get_cad_extract(_pool(request), job_id)
    nodes = (extract or {}).get("nodes", [])
    return [n for n in nodes if isinstance(n, dict) and n.get("excluded")]


class QuestionAnswerRequest(BaseModel):
    answer: Any


class SuggestionReviewRequest(BaseModel):
    review: Literal["accepted", "rejected", "suggested"] = "accepted"


class MappingConfirmRequest(BaseModel):
    columns: dict[str, Any]


async def _bom_file_path(pool: Pool, job_id: UUID) -> Path | None:
    s = get_settings()
    for u in await repo.list_uploads(pool, job_id):
        rel = await repo.get_upload_path(pool, job_id, u.id)
        if rel and Path(rel).suffix.lower() in [".xlsx", ".xls", ".csv"]:
            path = s.data_dir / rel
            return path if path.exists() else None
    return None


def _detect_mapping(job_id: UUID, bom_path: Path) -> TableMapping:
    from c2b_library.loader import load_library
    from c2b_rules.bom_intake import detect_and_map_bom

    s = get_settings()
    library, _ = load_library(s.library_dir)
    if not library:
        raise ProblemError(
            500, "internal_error", "Library error", "The rule library could not be loaded."
        )
    overrides = mapping_store.load_overrides(s.data_dir, job_id)
    mapping, _rows = detect_and_map_bom(bom_path, library, overrides=overrides)
    return mapping


@router.get("/jobs/{job_id}/mapping", tags=["jobs"])
async def get_job_mapping(
    job_id: UUID,
    job: JobRow,
    request: Request,
) -> TableMapping:
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    if bom_data and bom_data.get("mapping"):
        return TableMapping.model_validate(bom_data["mapping"])
    bom_path = await _bom_file_path(pool, job_id)
    if bom_path is None:
        raise ProblemError(
            404,
            "not_found",
            "No BoM file found",
            "Upload an Excel or CSV BoM file to map headers.",
        )
    return _detect_mapping(job_id, bom_path)


@router.post("/jobs/{job_id}/mapping/confirm", tags=["jobs"])
async def confirm_mapping(
    job_id: UUID,
    job: JobRow,
    body: MappingConfirmRequest,
    request: Request,
) -> dict[str, Any]:
    """Save the user's column choices. They apply to the next build of the BoM and, when a draft
    already exists, are recorded on it too (source=user)."""
    from c2b_library.loader import load_library
    from c2b_rules.dependency import apply_mapping_confirmation

    pool = _pool(request)
    s = get_settings()
    chosen: dict[str, str] = {}
    for col, info in body.columns.items():
        field = info.get("field") if isinstance(info, dict) else info
        if isinstance(field, str):
            chosen[col] = field

    library, _ = load_library(s.library_dir)
    if not library:
        raise ProblemError(
            500, "internal_error", "Library error", "The rule library could not be loaded."
        )
    allowed = set(library.headers.fields) | {"ignore"}
    bad = sorted(f for f in set(chosen.values()) if f not in allowed)
    if bad:
        raise ProblemError(
            422, "bad_field", "Unknown field", f"These are not BoM fields: {', '.join(bad)}."
        )

    bom_data = await repo.get_bom(pool, job_id)
    draft = BomDraft.model_validate(bom_data) if bom_data else None
    if draft is None or draft.mapping is None:
        bom_path = await _bom_file_path(pool, job_id)
        if bom_path is None:
            raise ProblemError(
                404, "not_found", "No BoM file found", "Upload an Excel or CSV BoM file first."
            )
        known = set(_detect_mapping(job_id, bom_path).columns)
    else:
        known = set(draft.mapping.columns)
    unknown = sorted(set(chosen) - known)
    if unknown:
        raise ProblemError(
            422, "bad_column", "Unknown column", f"The file has no column named {unknown[0]!r}."
        )

    rebuild_needed = False
    if draft is not None and draft.mapping is not None:
        for col, field in chosen.items():
            if draft.mapping.columns[col].field != field:
                rebuild_needed = True
            apply_mapping_confirmation(draft, col, field)
            for q in draft.questions:
                if q.mapping_column == col:
                    q.answer = field
        await repo.save_bom(pool, job_id, draft.model_dump(by_alias=True, mode="json"))
    mapping_store.save_overrides(s.data_dir, job_id, chosen)
    await repo.add_event(pool, job_id, "user_edit", "Confirmed column mapping")
    return {
        "status": "ok",
        "message": "Column mapping confirmed",
        "rebuild_needed": rebuild_needed,
    }


EDITABLE_FIELDS = frozenset(
    [
        "description",
        "material",
        "finish",
        "qty",
        "qty_per_parent",
        "class",
        "part_number",
        "make_buy",
        "uom",
    ]
)


class LineEditRequest(BaseModel):
    field: str
    value: Any


@router.post("/jobs/{job_id}/questions/{question_id}/answer", tags=["jobs"])
async def answer_question_endpoint(
    job_id: UUID,
    job: JobRow,
    question_id: str,
    body: QuestionAnswerRequest,
    request: Request,
) -> dict[str, Any]:
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    if not bom_data:
        raise ProblemError(
            404, "not_found", "BoM not found", "There is no draft BoM for this job yet."
        )

    from c2b_domain.bom import BomDraft
    from c2b_library.loader import load_library
    from c2b_rules.dependency import apply_question_answer

    library, _ = load_library(get_settings().library_dir)
    draft = BomDraft.model_validate(bom_data)
    asked = next((q for q in draft.questions if q.id == question_id), None)
    before_answer = asked.answer if asked else None
    affected = list(asked.line_ids) if asked else []
    try:
        draft = apply_question_answer(draft, question_id, body.answer, library)
    except ValueError as exc:
        raise ProblemError(404, "not_found", "Not found", str(exc)) from exc

    await _reopen_if_approved(pool, job_id, job)
    await repo.save_bom(pool, job_id, draft.model_dump(by_alias=True, mode="json"))
    await repo.add_edit_log(
        pool,
        job_id,
        "answer",
        f"answer to {question_id}",
        before_answer,
        body.answer,
        line_ids=affected,
        ref=question_id,
    )
    await repo.add_event(
        pool,
        job_id,
        "question_answered",
        f"Answered {question_id}: {body.answer}",
        {"question_id": question_id, "answer": body.answer},
    )
    return {"status": "ok", "question_id": question_id, "answer": body.answer}


@router.post("/jobs/{job_id}/suggestions/{suggestion_id}", tags=["jobs"])
async def review_suggestion_endpoint(
    job_id: UUID,
    job: JobRow,
    suggestion_id: str,
    body: SuggestionReviewRequest,
    request: Request,
) -> dict[str, Any]:
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    if not bom_data:
        raise ProblemError(
            404, "not_found", "BoM not found", "There is no draft BoM for this job yet."
        )

    from c2b_domain.bom import BomDraft
    from c2b_library.loader import load_library
    from c2b_rules.dependency import apply_suggestion_review

    library, _ = load_library(get_settings().library_dir)
    draft = BomDraft.model_validate(bom_data)
    proposal = next((s for s in draft.suggestions if s.id == suggestion_id), None)
    before_review = proposal.review if proposal else None
    affected_line = [proposal.line_id] if proposal and proposal.line_id else []
    try:
        draft = apply_suggestion_review(draft, suggestion_id, body.review, library)
    except ValueError as exc:
        raise ProblemError(404, "not_found", "Not found", str(exc)) from exc

    await _reopen_if_approved(pool, job_id, job)
    await repo.save_bom(pool, job_id, draft.model_dump(by_alias=True, mode="json"))
    await repo.add_edit_log(
        pool,
        job_id,
        "suggestion",
        f"suggestion {suggestion_id}",
        before_review,
        body.review,
        line_ids=affected_line,
        ref=suggestion_id,
    )
    await repo.add_event(
        pool,
        job_id,
        "suggestion_reviewed",
        f"Suggestion {suggestion_id} set to {body.review}",
        {"suggestion_id": suggestion_id, "review": body.review},
    )
    return {"status": "ok", "suggestion_id": suggestion_id, "review": body.review}


@router.patch("/jobs/{job_id}/lines/{line_id}", tags=["jobs"])
async def edit_line_endpoint(
    job_id: UUID,
    job: JobRow,
    line_id: str,
    body: LineEditRequest,
    request: Request,
) -> dict[str, Any]:
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    if not bom_data:
        raise ProblemError(
            404, "not_found", "BoM not found", "There is no draft BoM for this job yet."
        )

    from c2b_domain.bom import BomDraft
    from c2b_library.loader import load_library
    from c2b_rules.dependency import apply_user_line_edit, get_line_field

    if body.field not in EDITABLE_FIELDS:
        raise ProblemError(
            422,
            "invalid_field",
            "This field cannot be edited",
            f"Editable fields: {', '.join(sorted(EDITABLE_FIELDS))}.",
        )
    library, _ = load_library(get_settings().library_dir)
    draft = BomDraft.model_validate(bom_data)
    edited = next((ln for ln in draft.ebom if ln.id == line_id), None)
    before_value = get_line_field(edited, body.field) if edited else None
    try:
        draft = apply_user_line_edit(draft, line_id, body.field, body.value, library)
    except (ValueError, TypeError) as exc:
        raise ProblemError(404, "not_found", "Not found", str(exc)) from exc

    await _reopen_if_approved(pool, job_id, job)
    await repo.save_bom(pool, job_id, draft.model_dump(by_alias=True, mode="json"))
    await repo.add_edit_log(
        pool, job_id, "edit", body.field, before_value, body.value, line_ids=[line_id]
    )
    await repo.add_event(pool, job_id, "user_edit", f"Edited {line_id}.{body.field}")
    return {"status": "ok", "line_id": line_id, "field": body.field, "value": body.value}


@router.get("/jobs/{job_id}/lines/{line_id}/history", tags=["jobs"])
async def line_history_endpoint(
    job_id: UUID,
    job: JobRow,
    line_id: str,
    request: Request,
) -> list[dict[str, Any]]:
    """Who changed what on this line, newest first (before and after values)."""
    rows = await repo.list_edit_log(_pool(request), job_id, line_id)
    return [
        {
            "id": r["id"],
            "line_id": r["line_id"],
            "field": r["field"],
            "before": r["before"],
            "after": r["after"],
            "actor": r["actor"],
            "kind": r["kind"],
            "ref": r["ref"],
            "at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.post("/jobs/{job_id}/approve", tags=["jobs"])
async def approve_bom_endpoint(
    job_id: UUID,
    job: JobRow,
    request: Request,
) -> dict[str, Any]:
    """Validate and freeze an approved version of the BoM (docs/04 §11, docs/05 §4)."""
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    if not bom_data:
        raise ProblemError(
            404, "not_found", "BoM not found", "There is no draft BoM for this job yet."
        )

    from c2b_domain.bom import BomDraft
    from c2b_library.loader import load_library
    from c2b_rules.validator import validate_draft

    library, _ = load_library(get_settings().library_dir)
    draft = BomDraft.model_validate(bom_data)
    report = validate_draft(draft, library)

    if not report.valid:
        blocking_msgs = [f"[{i.code}] {i.message}" for i in report.blocking_issues]
        raise ProblemError(
            409,
            "approval_blocked",
            "BoM approval is blocked by unresolved items",
            "; ".join(blocking_msgs),
        )

    # Freeze an immutable version: the BomVersion plus what the reviewer decided (audit trail).
    import json
    from datetime import UTC, datetime

    from c2b_domain.bom import BomVersion
    from c2b_domain.cad import CadNode

    excluded: list[CadNode] = []
    extract = await repo.get_cad_extract(pool, job_id)
    for raw_node in (extract or {}).get("nodes", []):
        if isinstance(raw_node, dict) and raw_node.get("excluded"):
            excluded.append(CadNode.model_validate(raw_node))

    frozen = BomVersion(
        job_id=str(job_id),
        product=ProductInfo.model_validate(job["product"]),
        ebom=draft.ebom,
        mbom=draft.mbom,
        open_items=draft.open_items,
        excluded=excluded,
        questions=draft.questions,
        suggestions=draft.suggestions,
        validation=report,
        stats=draft.stats,
        library_version=draft.library_version,
        llm_calls=len(await repo.list_llm_calls(pool, job_id)),
        created_at=datetime.now(UTC).isoformat(),
    ).model_dump(by_alias=True, mode="json")

    # Approving twice without a change must not mint a new version.
    def _content(v: dict[str, Any]) -> str:
        skip = {"created_at", "version"}
        return json.dumps({k: x for k, x in v.items() if k not in skip}, sort_keys=True)

    existing = await repo.list_bom_versions(pool, job_id)
    if existing and _content(existing[-1]["data"]) == _content(frozen):
        version_num = int(existing[-1]["version"])
        unchanged = True
    else:
        version_num = await repo.freeze_bom_version(pool, job_id, frozen)
        unchanged = False
        await repo.add_event(pool, job_id, "bom_approved", f"Approved BoM version {version_num}")
    if job.get("status") != "approved":
        await repo.set_status(pool, job_id, "approved")
        await repo.add_event(pool, job_id, "status", "Approved", {"status": "approved"})
    return {
        "status": "approved",
        "version": version_num,
        "job_id": str(job_id),
        "unchanged": unchanged,
    }


@router.get("/jobs/{job_id}/versions", tags=["jobs"])
async def list_versions_endpoint(
    job_id: UUID,
    job: JobRow,
    request: Request,
) -> list[dict[str, Any]]:
    """One light row per frozen version; the full JSON is at /versions/{n}."""
    pool = _pool(request)
    rows = await repo.list_bom_versions(pool, job_id)
    return [
        {
            "version": r["version"],
            "created_at": r["created_at"].isoformat(),
            "ebom_lines": len(r["data"].get("ebom", [])),
            "mbom_lines": len(r["data"].get("mbom", [])),
            "open_items": len(r["data"].get("open_items", [])),
            "library_version": r["data"].get("library_version"),
        }
        for r in rows
    ]


@router.get("/jobs/{job_id}/versions/{version}", tags=["jobs"])
async def get_version_endpoint(
    job_id: UUID,
    job: JobRow,
    version: int,
    request: Request,
) -> dict[str, Any]:
    pool = _pool(request)
    v_data = await repo.get_bom_version(pool, job_id, version)
    if not v_data:
        raise ProblemError(
            404, "not_found", "Version not found", f"Version {version} does not exist."
        )
    return v_data


_EXPORT_TYPES = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "json": ("application/json", "json"),
    "schema": ("application/schema+json", "schema.json"),
    "pdf": ("application/pdf", "pdf"),
}


def _export_columns() -> Any:
    """Column order from the organisation profile (docs/05 §3); defaults when it has none."""
    from c2b_exports import ExportColumns
    from c2b_library.loader import load_library

    library, _ = load_library(get_settings().library_dir)
    profile = next(iter(library.profiles.values()), None) if library else None
    export = profile.export if profile else {}
    defaults = ExportColumns()
    return ExportColumns(
        ebom=list(export.get("ebom_columns", defaults.ebom)),
        mbom=list(export.get("mbom_columns", defaults.mbom)),
        csv=list(export.get("csv_columns", defaults.csv)),
    )


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:60] or "bom"


@router.get("/jobs/{job_id}/versions/{version}/export", tags=["jobs"])
async def export_version_endpoint(
    job_id: UUID,
    job: JobRow,
    version: int,
    request: Request,
    fmt: Annotated[Literal["xlsx", "csv", "json", "schema", "pdf"], Query()] = "xlsx",
    part: Annotated[Literal["ebom", "mbom"], Query()] = "ebom",
) -> Response:
    """Export a frozen version: XLSX workbook, CSV (EBOM or MBOM), JSON, its schema, PDF."""
    import asyncio

    import c2b_exports
    from c2b_domain.bom import BomVersion

    data = await repo.get_bom_version(_pool(request), job_id, version)
    if not data:
        raise ProblemError(
            404, "not_found", "Version not found", f"Version {version} does not exist."
        )
    ver = BomVersion.model_validate(data)
    media, ext = _EXPORT_TYPES[fmt]
    product = ver.product.name if ver.product else "bom"
    stem = f"{_safe_name(product)}_v{version}"
    if fmt == "csv":
        stem += f"_{part}"
    body: bytes
    if fmt == "xlsx":
        body = await asyncio.to_thread(c2b_exports.build_xlsx, ver, _export_columns())
    elif fmt == "csv":
        body = c2b_exports.build_csv(ver, part, _export_columns())
    elif fmt == "json":
        body = c2b_exports.build_json(data)
    elif fmt == "schema":
        body = c2b_exports.build_schema()
        stem = "bom_version"
    else:
        try:
            body = await asyncio.to_thread(c2b_exports.build_pdf, ver)
        except OSError as exc:  # pango missing in a non-container run
            raise ProblemError(
                503,
                "pdf_unavailable",
                "PDF export is not available",
                "The PDF engine (WeasyPrint with pango) is not installed on this server.",
            ) from exc
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'},
    )


@router.post("/jobs/{job_id}/chat", tags=["jobs"])
async def chat_endpoint(
    job_id: UUID,
    job: JobRow,
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Agent chat grounded in job data; change proposals return as suggestions only (docs/05 §4)."""
    pool = _pool(request)
    bom_data = await repo.get_bom(pool, job_id)
    draft: BomDraft | None = None
    if bom_data:
        draft = BomDraft.model_validate(bom_data)

    from c2b_agents.agent import ChatAgent

    agent = ChatAgent()
    response = await agent.chat(
        job_id=str(job_id),
        message=body.message,
        draft=draft,
        history=body.history,
    )

    # If new suggestions were proposed, persist them to the draft BoM
    if response.suggestions and draft:
        existing_s_ids = {s.id for s in draft.suggestions}
        added_count = 0
        for s in response.suggestions:
            if s.id not in existing_s_ids:
                draft.suggestions.append(s)
                added_count += 1
        if added_count > 0:
            await repo.save_bom(pool, job_id, draft.model_dump(by_alias=True, mode="json"))
            await repo.add_event(
                pool,
                job_id,
                "suggestion_proposed",
                f"Agent proposed {added_count} suggestion(s) from chat",
                {"count": added_count, "suggestions": [s.id for s in response.suggestions]},
            )

    return response


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
