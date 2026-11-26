"""Prometheus text metrics (ADR-022). No client library: the format is a few lines of text."""

import shutil

from c2b_api import ratelimit, retention
from c2b_jobs import repo
from c2b_jobs.repo import Pool
from c2b_jobs.settings import get_settings

ALL_STATUSES = ("uploading", "converting", "extracting", "building", "review", "approved", "failed")


def _line(name: str, value: float | int, labels: dict[str, str] | None = None) -> str:
    lab = ""
    if labels:
        lab = "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
    return f"{name}{lab} {value}"


async def render(pool: Pool) -> str:
    s = get_settings()
    out: list[str] = []

    def head(name: str, help_: str, kind: str = "gauge") -> None:
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {kind}")

    counts = await repo.job_counts(pool)
    head("c2b_jobs", "Jobs by status")
    for st in ALL_STATUSES:
        out.append(_line("c2b_jobs", counts.get(st, 0), {"status": st}))

    depth = await repo.queue_depth(pool)
    head("c2b_queue_waiting", "Tasks waiting per queue")
    for q in ("pipeline", "cad", "native"):
        out.append(_line("c2b_queue_waiting", depth.get(q, 0), {"queue": q}))

    head("c2b_aps_jobs_used", "APS translation jobs used this month")
    out.append(_line("c2b_aps_jobs_used", await repo.aps_usage(pool)))
    head("c2b_aps_jobs_cap", "Configured monthly APS cap")
    out.append(_line("c2b_aps_jobs_cap", s.aps_monthly_cap))

    usage = shutil.disk_usage(s.data_dir) if s.data_dir.exists() else None
    if usage:
        head("c2b_data_disk_free_bytes", "Free space on the data volume")
        out.append(_line("c2b_data_disk_free_bytes", usage.free))
        head("c2b_data_disk_total_bytes", "Size of the data volume")
        out.append(_line("c2b_data_disk_total_bytes", usage.total))

    head("c2b_retention_deleted_total", "Jobs deleted by the retention sweep", "counter")
    out.append(_line("c2b_retention_deleted_total", retention.STATS["deleted_total"]))
    head("c2b_retention_errors_total", "Retention sweep errors", "counter")
    out.append(_line("c2b_retention_errors_total", retention.STATS["errors_total"]))
    head("c2b_rate_limited_total", "Requests answered 429", "counter")
    out.append(_line("c2b_rate_limited_total", ratelimit.STATS["limited_total"]))
    return "\n".join(out) + "\n"
