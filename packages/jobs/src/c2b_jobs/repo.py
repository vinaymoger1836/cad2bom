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
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job (id, token_hash, status, product, library_version)"
            " VALUES (%s, %s, 'uploading', %s, %s)",
            (job_id, token_hash, Jsonb(product.model_dump(mode="json")), library_version),
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
