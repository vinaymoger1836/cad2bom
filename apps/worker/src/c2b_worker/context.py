"""Process-wide handles set by main() before the worker starts taking jobs."""

from c2b_jobs.repo import Pool

_pool: Pool | None = None


def set_pool(pool: Pool) -> None:
    global _pool
    _pool = pool


def pool() -> Pool:
    if _pool is None:
        raise RuntimeError("worker database pool is not initialised")
    return _pool
