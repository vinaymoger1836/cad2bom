"""Admin: library versions (browse, draft, validate, publish, diff) and metrics (ADR-019, ADR-020).

Two layers protect it. Caddy asks for HTTP basic auth on `/admin*` and `/api/admin/*` (reverse-
proxy auth). Behind that, every call needs a short-lived session token from `POST /admin/login`,
which checks the admin passcode. The token travels in `X-Admin-Token` because the browser already
uses `Authorization` for the basic-auth credentials. With no `C2B_ADMIN_PASSCODE` set, the whole
admin API answers 404 as if it did not exist.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from c2b_api import metrics
from c2b_api.errors import ProblemError
from c2b_api.ratelimit import client_ip
from c2b_jobs import repo
from c2b_jobs.settings import get_settings
from c2b_library import versions
from c2b_library.access import VersionError

log = logging.getLogger("c2b.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


def _secret() -> bytes:
    s = get_settings()
    material = (
        s.admin_secret or hashlib.sha256(b"c2b-admin:" + s.admin_passcode.encode()).hexdigest()
    )
    return material.encode()


def issue_token(now: float | None = None) -> tuple[str, int]:
    ttl = get_settings().admin_session_s
    expires = int((now or time.time()) + ttl)
    payload = str(expires).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    token = (
        base64.urlsafe_b64encode(payload).decode() + "." + base64.urlsafe_b64encode(sig).decode()
    )
    return token, ttl


def token_valid(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    try:
        p64, s64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(p64.encode())
        sig = base64.urlsafe_b64decode(s64.encode())
        expires = int(payload.decode())
    except (ValueError, UnicodeDecodeError):
        return False
    good = hmac.compare_digest(sig, hmac.new(_secret(), payload, hashlib.sha256).digest())
    return good and expires > (now or time.time())


async def require_admin(
    request: Request,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    s = get_settings()
    if not s.admin_enabled:
        raise ProblemError(404, "not_found", "Not found", "There is nothing here.")
    if not token_valid(x_admin_token):
        raise ProblemError(
            401,
            "unauthorized",
            "Admin sign-in needed",
            "Sign in with the admin passcode to continue.",
        )


Admin = Annotated[None, Depends(require_admin)]


def _pool(request: Request) -> repo.Pool:
    pool: repo.Pool = request.app.state.pool
    return pool


class LoginBody(BaseModel):
    passcode: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(body: LoginBody, request: Request) -> dict[str, Any]:
    s = get_settings()
    if not s.admin_enabled:
        raise ProblemError(404, "not_found", "Not found", "There is nothing here.")
    ip = client_ip(request.scope)
    ok = hmac.compare_digest(
        hashlib.sha256(body.passcode.encode()).digest(),
        hashlib.sha256(s.admin_passcode.encode()).digest(),
    )
    await repo.add_admin_audit(_pool(request), "login_ok" if ok else "login_failed", ip=ip)
    if not ok:
        await asyncio.sleep(0.5)  # slows guessing; the rate limiter does the real work
        raise ProblemError(401, "unauthorized", "Wrong passcode", "That passcode is not right.")
    token, ttl = issue_token()
    return {"token": token, "expires_in": ttl}


@router.get("/status")
async def status(request: Request, _: Admin) -> dict[str, Any]:
    s = get_settings()
    return {
        "library_active": versions.active_version(s.library_store_dir, s.library_dir),
        "aps_configured": s.aps_configured,
        "aps_mode": s.aps_mode,
        "aps_usage": {"used": await repo.aps_usage(_pool(request)), "cap": s.aps_monthly_cap},
        "retention_days": s.retention_days,
        "environment": s.environment,
    }


@router.get("/metrics")
async def metrics_endpoint(
    request: Request,
    x_admin_token: Annotated[str | None, Header()] = None,
    x_metrics_token: Annotated[str | None, Header()] = None,
) -> Any:
    """Prometheus text. Allowed with an admin session, or with `X-Metrics-Token` for scrapers."""
    from fastapi.responses import PlainTextResponse

    s = get_settings()
    if not s.admin_enabled and not s.metrics_token:
        raise ProblemError(404, "not_found", "Not found", "There is nothing here.")
    metrics_ok = bool(s.metrics_token) and hmac.compare_digest(
        (x_metrics_token or "").encode(), s.metrics_token.encode()
    )
    if not (metrics_ok or (s.admin_enabled and token_valid(x_admin_token))):
        raise ProblemError(401, "unauthorized", "Not allowed", "A valid token is needed.")
    text = await metrics.render(_pool(request))
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4; charset=utf-8")


# --- library -------------------------------------------------------------------------------


def _store() -> tuple[Any, Any]:
    s = get_settings()
    return s.library_store_dir, s.library_dir


def _wrap(exc: VersionError) -> ProblemError:
    return ProblemError(422, "invalid_request", "Library change refused", str(exc))


@router.get("/library/versions")
async def list_versions(_: Admin) -> dict[str, Any]:
    store, base = _store()
    vs = await asyncio.to_thread(versions.list_versions, store, base)
    return {
        "active": versions.active_version(store, base),
        "versions": [v.__dict__ for v in vs],
        "drafts": versions.list_drafts(store),
        "next_version": versions.next_version(store, base),
    }


@router.get("/library/versions/{version}/files")
async def version_files(version: str, _: Admin) -> list[str]:
    store, base = _store()
    try:
        return versions.list_files(versions.version_path(store, base, version))
    except VersionError as exc:
        raise _wrap(exc) from exc


@router.get("/library/versions/{version}/file")
async def version_file(version: str, path: Annotated[str, Query()], _: Admin) -> dict[str, str]:
    store, base = _store()
    try:
        root = versions.version_path(store, base, version)
        return {"path": path, "content": versions.read_file(root, path)}
    except VersionError as exc:
        raise _wrap(exc) from exc


class DraftCreate(BaseModel):
    from_version: str


@router.post("/library/drafts", status_code=201)
async def create_draft(body: DraftCreate, request: Request, _: Admin) -> dict[str, str]:
    store, base = _store()
    try:
        draft_id = await asyncio.to_thread(versions.create_draft, store, base, body.from_version)
    except VersionError as exc:
        raise _wrap(exc) from exc
    await repo.add_admin_audit(
        _pool(request), "draft_created", {"draft": draft_id, "from": body.from_version}
    )
    return {"id": draft_id, "base": body.from_version}


@router.get("/library/drafts/{draft_id}/files")
async def draft_files(draft_id: str, _: Admin) -> list[str]:
    store, _b = _store()
    try:
        return versions.list_files(versions.draft_path(store, draft_id))
    except VersionError as exc:
        raise _wrap(exc) from exc


@router.get("/library/drafts/{draft_id}/file")
async def draft_file(draft_id: str, path: Annotated[str, Query()], _: Admin) -> dict[str, str]:
    store, _b = _store()
    try:
        return {
            "path": path,
            "content": versions.read_file(versions.draft_path(store, draft_id), path),
        }
    except VersionError as exc:
        raise _wrap(exc) from exc


class FileBody(BaseModel):
    content: Annotated[str, Field(max_length=versions.MAX_FILE_BYTES)]


@router.put("/library/drafts/{draft_id}/file")
async def put_draft_file(
    draft_id: str, path: Annotated[str, Query()], body: FileBody, request: Request, _: Admin
) -> dict[str, str]:
    store, _b = _store()
    try:
        versions.write_draft_file(store, draft_id, path, body.content)
    except VersionError as exc:
        raise ProblemError(422, "invalid_request", "Not saved", f"{path}: {exc}") from exc
    await repo.add_admin_audit(
        _pool(request), "draft_file_saved", {"draft": draft_id, "file": path}
    )
    return {"status": "saved", "path": path}


@router.post("/library/drafts/{draft_id}/validate")
async def validate(draft_id: str, _: Admin) -> dict[str, Any]:
    store, _b = _store()
    try:
        ok, issues = await asyncio.to_thread(versions.validate_draft, store, draft_id)
    except VersionError as exc:
        raise _wrap(exc) from exc
    return {
        "ok": ok,
        "errors": sum(i["level"] == "error" for i in issues),
        "warnings": sum(i["level"] == "warning" for i in issues),
        "issues": issues,
    }


class PublishBody(BaseModel):
    changelog: Annotated[
        list[Annotated[str, Field(max_length=500)]], Field(min_length=1, max_length=20)
    ]


@router.post("/library/drafts/{draft_id}/publish")
async def publish(draft_id: str, body: PublishBody, request: Request, _: Admin) -> dict[str, Any]:
    store, base = _store()
    try:
        version = await asyncio.to_thread(
            versions.publish_draft, store, base, draft_id, body.changelog
        )
    except VersionError as exc:
        raise _wrap(exc) from exc
    await asyncio.to_thread(_record_in_database, version)
    await repo.add_admin_audit(
        _pool(request), "library_published", {"version": version, "changelog": body.changelog}
    )
    log.info("library %s published", version)
    return {"version": version, "active": True}


def _record_in_database(version: str) -> None:
    """Also load the new version into Postgres (library search). Best effort: the files in the
    store are what jobs read."""
    import psycopg

    from c2b_library.db import store as db_store
    from c2b_library.loader import load_library

    s = get_settings()
    lib, _ = load_library(versions.version_path(s.library_store_dir, s.library_dir, version))
    if lib is None:
        return
    try:
        with psycopg.connect(s.database_url) as conn:
            db_store(lib, conn)
    except Exception:
        log.exception("could not record library %s in the database", version)


@router.delete("/library/drafts/{draft_id}")
async def drop_draft(draft_id: str, request: Request, _: Admin) -> dict[str, str]:
    store, _b = _store()
    try:
        versions.delete_draft(store, draft_id)
    except VersionError as exc:
        raise _wrap(exc) from exc
    await repo.add_admin_audit(_pool(request), "draft_deleted", {"draft": draft_id})
    return {"status": "deleted"}


def _resolve_side(ref: str) -> Any:
    store, base = _store()
    if ref.startswith("draft:"):
        return versions.draft_path(store, ref.removeprefix("draft:"))
    return versions.version_path(store, base, ref)


@router.get("/library/diff")
async def diff(a: Annotated[str, Query()], b: Annotated[str, Query()], _: Admin) -> dict[str, Any]:
    """Compare two versions (`2026.11.0`) or a version and a draft (`draft:<id>`)."""
    try:
        pa, pb = _resolve_side(a), _resolve_side(b)
        changes = await asyncio.to_thread(versions.diff_versions, pa, pb)
    except VersionError as exc:
        raise _wrap(exc) from exc
    return {
        "a": a,
        "b": b,
        "files_changed": len(changes),
        "added": sum(int(c["added"]) for c in changes),  # type: ignore[call-overload]
        "removed": sum(int(c["removed"]) for c in changes),  # type: ignore[call-overload]
        "changes": changes,
    }
