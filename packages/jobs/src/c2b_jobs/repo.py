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
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job"
            " (id, token_hash, status, product, library_version, consent_native_conversion)"
            " VALUES (%s, %s, 'uploading', %s, %s, %s)",
            (
                job_id,
                token_hash,
                Jsonb(product.model_dump(mode="json")),
                library_version,
                consent_native_conversion,
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
) -> UploadOut:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO upload"
            " (id, job_id, filename, kind, detected_type, size_bytes, sha256, stored_path)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id, filename, kind, detected_type, size_bytes, sha256, created_at",
            (upload_id, job_id, filename, kind, detected_type, size_bytes, sha256, stored_path),
        )
        row = await cur.fetchone()
        assert row is not None
        return UploadOut.model_validate(row)


async def list_uploads(pool: Pool, job_id: UUID) -> list[UploadOut]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, filename, kind, detected_type, size_bytes, sha256, created_at"
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


async def first_cad_upload(pool: Pool, job_id: UUID) -> UUID | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM upload WHERE job_id = %s AND kind = 'cad'"
            " ORDER BY created_at, id LIMIT 1",
            (job_id,),
        )
        row = await cur.fetchone()
        return None if row is None else UUID(str(row["id"]))


async def set_consent_native_conversion(pool: Pool, job_id: UUID, consent: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE job SET consent_native_conversion = %s, updated_at = now() WHERE id = %s",
            (consent, job_id),
        )


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
