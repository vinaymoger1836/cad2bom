"""Retention sweep and delete-now (ADR-021).

One function removes a job everywhere (files first, then the row, whose dependants cascade), and
both the hourly sweep and the delete-now endpoint use it. Files are removed first on purpose: a
failure leaves the row so the sweep retries, instead of leaving files that nothing points to.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from uuid import UUID

from c2b_jobs import repo
from c2b_jobs.repo import Pool
from c2b_jobs.settings import get_settings

log = logging.getLogger("c2b.retention")

SWEEP_LOCK_KEY = 727002  # advisory lock: one sweep at a time across replicas

# Counters shown on /metrics.
STATS = {"deleted_total": 0, "sweeps_total": 0, "errors_total": 0}


def _job_dirs(job_id: UUID) -> list[Path]:
    s = get_settings()
    sid = str(job_id)
    return [s.data_dir / "jobs" / sid, s.data_dir / "out" / sid, s.converted_dir / sid]


def _remove_files(job_id: UUID) -> None:
    for d in _job_dirs(job_id):
        if d.exists():
            shutil.rmtree(d)


async def delete_job_everywhere(pool: Pool, job_id: UUID) -> bool:
    """Delete files, then the job row (uploads, events, BoMs, edit log cascade). Idempotent."""
    await asyncio.to_thread(_remove_files, job_id)
    return await repo.delete_job(pool, job_id)


async def sweep_once(pool: Pool) -> int:
    """Delete every expired job. Returns how many were removed."""
    removed = 0
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (SWEEP_LOCK_KEY,))
        row = await cur.fetchone()
        if not row or not row["got"]:
            return 0
        try:
            for job_id in await repo.expired_job_ids(pool):
                try:
                    if await delete_job_everywhere(pool, job_id):
                        removed += 1
                except Exception:
                    STATS["errors_total"] += 1
                    log.exception("retention: could not delete job %s", job_id)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (SWEEP_LOCK_KEY,))
    STATS["sweeps_total"] += 1
    STATS["deleted_total"] += removed
    if removed:
        log.info("retention: deleted %d expired job(s)", removed)
    return removed


async def sweep_loop(pool: Pool) -> None:
    """Run the sweep every `C2B_RETENTION_SWEEP_S` seconds until cancelled."""
    interval = max(60, get_settings().retention_sweep_s)
    while True:
        try:
            await sweep_once(pool)
        except Exception:
            STATS["errors_total"] += 1
            log.exception("retention sweep failed")
        await asyncio.sleep(interval)
