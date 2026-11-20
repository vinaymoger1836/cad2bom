"""Apply SQL migrations and the procrastinate schema, serialised by an advisory lock.

Run by the API at startup and available as `c2b-migrate`. Safe to run from several containers
at once: the second caller waits for the lock and then finds nothing to do.
"""

import logging
from pathlib import Path

import psycopg

from c2b_jobs.queue import app
from c2b_jobs.settings import get_settings

log = logging.getLogger("c2b.migrate")

_LOCK_ID = 727_001
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(database_url: str | None = None) -> list[str]:
    """Apply pending migrations. Returns the names applied in this call."""
    url = database_url or get_settings().database_url
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migration").fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                    conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
                log.info("applied migration %s", path.name)

            has_queue = conn.execute("SELECT to_regclass('public.procrastinate_jobs')").fetchone()
            if has_queue is None or has_queue[0] is None:
                with conn.transaction():
                    conn.execute(app.schema_manager.get_schema())  # type: ignore[arg-type]
                applied.append("procrastinate-schema")
                log.info("applied procrastinate schema")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    done = run_migrations()
    print("applied:", ", ".join(done) if done else "nothing to do")


if __name__ == "__main__":
    main()
