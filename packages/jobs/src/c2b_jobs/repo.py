"""Small SQL repository for jobs, uploads and events. Plain psycopg, no ORM."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from c2b_domain import JobEvent, ProductInfo, UploadOut

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def make_pool(database_url: str) -> Pool:
    return AsyncConnectionPool[AsyncConnection[DictRow]](
        database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row},
    )


async def create_job(
    pool: Pool,
    job_id: UUID,
    product: ProductInfo,
    token_hash: bytes,
    library_version: str,
    consent_native_conversion: bool = False,
    retention_days: int = 30,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job"
            " (id, token_hash, status, product, library_version, consent_native_conversion,"
            "  consent_native_at, expires_at)"
            " VALUES (%s, %s, 'uploading', %s, %s, %s,"
            "  CASE WHEN %s THEN now() END, now() + make_interval(days => %s))",
            (
                job_id,
                token_hash,
                Jsonb(product.model_dump(mode="json")),
                library_version,
                consent_native_conversion,
                consent_native_conversion,
                retention_days,
            ),
        )


async def get_job(pool: Pool, job_id: UUID) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM job WHERE id = %s", (job_id,))
        return await cur.fetchone()


async def set_status(pool: Pool, job_id: UUID, status: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE job SET status = %s, updated_at = now() WHERE id = %s", (status, job_id)
        )


async def claim_start(pool: Pool, job_id: UUID) -> bool:
    """Atomically move uploading -> extracting. False if the job was already started."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE job SET status = 'extracting', updated_at = now()"
            " WHERE id = %s AND status = 'uploading' RETURNING id",
            (job_id,),
        )
        return await cur.fetchone() is not None


async def seconds_since_last_event(pool: Pool, job_id: UUID) -> float | None:
    """Seconds since the job last reported anything (None if it never did)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT EXTRACT(EPOCH FROM (now() - max(created_at))) AS idle"
            " FROM job_event WHERE job_id = %s",
            (job_id,),
        )
        row = await cur.fetchone()
    idle = row["idle"] if row else None
    return float(idle) if idle is not None else None


async def add_event(
    pool: Pool,
    job_id: UUID,
    kind: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO job_event (job_id, kind, message, data)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (job_id, kind, message, Jsonb(data or {})),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row["id"])


async def events_after(pool: Pool, job_id: UUID, after_id: int, limit: int = 200) -> list[JobEvent]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, job_id, kind, message, data, created_at FROM job_event"
            " WHERE job_id = %s AND id > %s ORDER BY id LIMIT %s",
            (job_id, after_id, limit),
        )
        return [JobEvent.model_validate(r) for r in await cur.fetchall()]


async def add_upload(
    pool: Pool,
    *,
    upload_id: UUID,
    job_id: UUID,
    filename: str,
    kind: str,
    detected_type: str,
    size_bytes: int,
    sha256: str,
    stored_path: str,
    origin: str = "user",
    derived_from: UUID | None = None,
) -> UploadOut:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO upload"
            " (id, job_id, filename, kind, detected_type, size_bytes, sha256, stored_path,"
            "  origin, derived_from)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id, filename, kind, detected_type, size_bytes, sha256, origin, created_at",
            (
                upload_id,
                job_id,
                filename,
                kind,
                detected_type,
                size_bytes,
                sha256,
                stored_path,
                origin,
                derived_from,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return UploadOut.model_validate(row)


async def list_uploads(pool: Pool, job_id: UUID) -> list[UploadOut]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, filename, kind, detected_type, size_bytes, sha256, origin, created_at"
            " FROM upload WHERE job_id = %s ORDER BY created_at, id",
            (job_id,),
        )
        return [UploadOut.model_validate(r) for r in await cur.fetchall()]


async def get_upload_path(pool: Pool, job_id: UUID, upload_id: UUID) -> str | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT stored_path FROM upload WHERE id = %s AND job_id = %s", (upload_id, job_id)
        )
        row = await cur.fetchone()
        return None if row is None else str(row["stored_path"])


async def count_uploads(pool: Pool, job_id: UUID) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM upload WHERE job_id = %s", (job_id,))
        row = await cur.fetchone()
        assert row is not None
        return int(row["n"])


NATIVE_TYPES = ("f3d", "sldprt", "sldasm")


async def first_cad_upload(pool: Pool, job_id: UUID) -> UUID | None:
    """The first STEP to read: a converted one wins, then a user STEP. Native files are never
    read directly; they are converted first."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM upload WHERE job_id = %s AND kind = 'cad'"
            " AND detected_type <> ALL(%s)"
            " ORDER BY (origin = 'converted') DESC, created_at, id LIMIT 1",
            (job_id, list(NATIVE_TYPES)),
        )
        row = await cur.fetchone()
        return None if row is None else UUID(str(row["id"]))


async def first_native_upload(pool: Pool, job_id: UUID) -> UUID | None:
    """The first native CAD upload that has not been converted yet."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT u.id FROM upload u WHERE u.job_id = %s AND u.kind = 'cad'"
            " AND u.detected_type = ANY(%s)"
            " AND NOT EXISTS (SELECT 1 FROM job_native n WHERE n.upload_id = u.id)"
            " ORDER BY u.created_at, u.id LIMIT 1",
            (job_id, list(NATIVE_TYPES)),
        )
        row = await cur.fetchone()
        return None if row is None else UUID(str(row["id"]))


async def get_upload(pool: Pool, job_id: UUID, upload_id: UUID) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM upload WHERE id = %s AND job_id = %s", (upload_id, job_id)
        )
        return await cur.fetchone()


async def set_consent_native_conversion(
    pool: Pool, job_id: UUID, consent: bool, text_version: str | None = None
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE job SET consent_native_conversion = %s,"
            " consent_native_at = CASE WHEN %s THEN now() END,"
            " consent_native_text_ver = CASE WHEN %s THEN %s END,"
            " updated_at = now() WHERE id = %s",
            (consent, consent, consent, text_version, job_id),
        )


async def extend_expiry(pool: Pool, job_id: UUID, retention_days: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE job SET expires_at = now() + make_interval(days => %s) WHERE id = %s",
            (retention_days, job_id),
        )


async def delete_job(pool: Pool, job_id: UUID) -> bool:
    """Delete the job row; every dependent table cascades. Files are removed by the caller."""
    async with pool.connection() as conn:
        cur = await conn.execute("DELETE FROM job WHERE id = %s RETURNING id", (job_id,))
        return await cur.fetchone() is not None


async def expired_job_ids(pool: Pool, limit: int = 200) -> list[UUID]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM job WHERE expires_at IS NOT NULL AND expires_at < now()"
            " ORDER BY expires_at LIMIT %s",
            (limit,),
        )
        return [UUID(str(r["id"])) for r in await cur.fetchall()]


# --- APS usage (free-tier cap) and native conversion results -------------------------------


def _month() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m")


async def aps_usage(pool: Pool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT jobs FROM aps_usage WHERE month = %s", (_month(),))
        row = await cur.fetchone()
        return int(row["jobs"]) if row else 0


async def aps_reserve(pool: Pool, jobs: int, cap: int) -> bool:
    """Atomically add `jobs` to this month's count unless that would pass the cap."""
    month = _month()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO aps_usage (month, jobs) VALUES (%s, 0) ON CONFLICT (month) DO NOTHING",
            (month,),
        )
        cur = await conn.execute(
            "UPDATE aps_usage SET jobs = jobs + %s, updated_at = now()"
            " WHERE month = %s AND jobs + %s <= %s RETURNING jobs",
            (jobs, month, jobs, cap),
        )
        return await cur.fetchone() is not None


async def aps_refund(pool: Pool, jobs: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE aps_usage SET jobs = GREATEST(jobs - %s, 0), updated_at = now()"
            " WHERE month = %s",
            (jobs, _month()),
        )


async def save_job_native(
    pool: Pool,
    job_id: UUID,
    upload_id: UUID,
    *,
    source_format: str,
    status: str,
    message: str,
    usage_jobs: int,
    properties: list[dict[str, Any]],
    obj_relpaths: list[str],
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job_native (job_id, upload_id, source_format, status, message,"
            " usage_jobs, properties, obj_relpaths) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (job_id, upload_id) DO UPDATE SET status = EXCLUDED.status,"
            " message = EXCLUDED.message, usage_jobs = EXCLUDED.usage_jobs,"
            " properties = EXCLUDED.properties, obj_relpaths = EXCLUDED.obj_relpaths",
            (
                job_id,
                upload_id,
                source_format,
                status,
                message,
                usage_jobs,
                Jsonb(properties),
                Jsonb(obj_relpaths),
            ),
        )


async def list_job_native(pool: Pool, job_id: UUID) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM job_native WHERE job_id = %s ORDER BY created_at", (job_id,)
        )
        return list(await cur.fetchall())


async def add_admin_audit(
    pool: Pool, action: str, detail: dict[str, Any] | None = None, ip: str | None = None
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO admin_audit (action, detail, ip) VALUES (%s, %s, %s)",
            (action, Jsonb(detail or {}), ip),
        )


async def job_counts(pool: Pool) -> dict[str, int]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status, count(*) AS n FROM job GROUP BY status")
        return {str(r["status"]): int(r["n"]) for r in await cur.fetchall()}


async def queue_depth(pool: Pool) -> dict[str, int]:
    """Waiting procrastinate jobs per queue (empty when the schema is not there yet)."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT queue_name, count(*) AS n FROM procrastinate_jobs"
                " WHERE status = 'todo' GROUP BY queue_name"
            )
            return {str(r["queue_name"]): int(r["n"]) for r in await cur.fetchall()}
    except Exception:
        return {}


async def save_cad_extract(
    pool: Pool,
    job_id: UUID,
    upload_id: UUID,
    data: dict[str, Any],
    leaf_count: int = 0,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO cad_extract (job_id, upload_id, data, leaf_count)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (job_id, upload_id) DO UPDATE"
            " SET data = EXCLUDED.data, leaf_count = EXCLUDED.leaf_count, created_at = now()",
            (job_id, upload_id, Jsonb(data), leaf_count),
        )


async def get_cad_extract(pool: Pool, job_id: UUID) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT data FROM cad_extract WHERE job_id = %s ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row["data"])


async def save_bom(
    pool: Pool,
    job_id: UUID,
    data: dict[str, Any],
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job_bom (job_id, data, updated_at)"
            " VALUES (%s, %s, now())"
            " ON CONFLICT (job_id) DO UPDATE"
            " SET data = EXCLUDED.data, updated_at = now()",
            (job_id, Jsonb(data)),
        )


async def get_bom(pool: Pool, job_id: UUID) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT data FROM job_bom WHERE job_id = %s",
            (job_id,),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row["data"])


async def answer_question(
    pool: Pool,
    job_id: UUID,
    question_id: str,
    answer: Any,
) -> bool:
    """Record an answer to a question in the draft BoM."""
    bom = await get_bom(pool, job_id)
    if not bom:
        return False
    questions = bom.get("questions") or []
    updated = False
    for q in questions:
        if q.get("id") == question_id:
            q["answer"] = answer
            updated = True
            break
    if updated:
        bom["questions"] = questions
        await save_bom(pool, job_id, bom)
    return updated


async def review_suggestion(
    pool: Pool,
    job_id: UUID,
    suggestion_id: str,
    review: str,
) -> bool:
    """Record accept or reject on a suggestion in the draft BoM."""
    bom = await get_bom(pool, job_id)
    if not bom:
        return False
    suggestions = bom.get("suggestions") or []
    updated = False
    for s in suggestions:
        if s.get("id") == suggestion_id:
            s["review"] = review
            updated = True
            break
    if updated:
        bom["suggestions"] = suggestions
        await save_bom(pool, job_id, bom)
    return updated


async def record_llm_call(
    pool: Pool,
    job_id: UUID,
    role: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: int,
    fallback_reason: str,
    line_id: str | None = None,
    cassette_key: str | None = None,
) -> int:
    """Insert an audited record of an LLM call into llm_call table."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO llm_call (
                job_id, role, model, prompt_tokens, completion_tokens,
                total_tokens, latency_ms, fallback_reason, line_id, cassette_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                job_id,
                role,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                latency_ms,
                fallback_reason,
                line_id,
                cassette_key,
            ),
        )
        row = await cur.fetchone()
        return int(row["id"]) if row else 0


async def list_llm_calls(
    pool: Pool,
    job_id: UUID,
) -> list[dict[str, Any]]:
    """List all LLM calls for a job ordered chronologically."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM llm_call WHERE job_id = %s ORDER BY id ASC",
            (job_id,),
        )
        rows = await cur.fetchall()
        return list(rows)


async def add_edit_log(
    pool: Pool,
    job_id: UUID,
    kind: str,
    field: str,
    before: Any,
    after: Any,
    line_ids: list[str] | None = None,
    ref: str | None = None,
    actor: str = "user",
) -> None:
    """Record one user decision with before and after values (one row per affected line)."""
    targets: list[str | None] = list(line_ids) if line_ids else [None]
    async with pool.connection() as conn:
        for line_id in targets:
            await conn.execute(
                "INSERT INTO edit_log (job_id, line_id, field, before, after, actor, kind, ref)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (job_id, line_id, field, Jsonb(before), Jsonb(after), actor, kind, ref),
            )


async def list_edit_log(
    pool: Pool,
    job_id: UUID,
    line_id: str | None = None,
) -> list[dict[str, Any]]:
    """History for one line (or the whole job), newest first."""
    async with pool.connection() as conn:
        if line_id is None:
            cur = await conn.execute(
                "SELECT id, line_id, field, before, after, actor, kind, ref, created_at"
                " FROM edit_log WHERE job_id = %s ORDER BY id DESC",
                (job_id,),
            )
        else:
            cur = await conn.execute(
                "SELECT id, line_id, field, before, after, actor, kind, ref, created_at"
                " FROM edit_log WHERE job_id = %s AND line_id = %s ORDER BY id DESC",
                (job_id, line_id),
            )
        return list(await cur.fetchall())


async def freeze_bom_version(
    pool: Pool,
    job_id: UUID,
    data: dict[str, Any],
) -> int:
    """Freeze an approved draft BoM as a new immutable version in bom_version."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_ver FROM bom_version WHERE job_id = %s",
            (job_id,),
        )
        row = await cur.fetchone()
        next_ver = int(row["next_ver"]) if row else 1

        data["version"] = next_ver
        await conn.execute(
            """
            INSERT INTO bom_version (job_id, version, data, created_at)
            VALUES (%s, %s, %s, now())
            """,
            (job_id, next_ver, Jsonb(data)),
        )
        return next_ver


async def list_bom_versions(
    pool: Pool,
    job_id: UUID,
) -> list[dict[str, Any]]:
    """List all frozen BoM versions for a job."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT version, created_at, data FROM bom_version "
            "WHERE job_id = %s ORDER BY version ASC",
            (job_id,),
        )
        rows = await cur.fetchall()
        return list(rows)


async def get_bom_version(
    pool: Pool,
    job_id: UUID,
    version: int,
) -> dict[str, Any] | None:
    """Get a specific frozen BoM version for a job."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT data FROM bom_version WHERE job_id = %s AND version = %s",
            (job_id, version),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row["data"])
