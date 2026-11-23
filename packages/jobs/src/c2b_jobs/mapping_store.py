"""The user's confirmed BoM-file column mapping, kept per job (source=user, never overwritten).

Stored as `<data>/jobs/<id>/mapping_overrides.json` (column header -> canonical field) so it
survives re-runs and can be saved before the pipeline has produced a draft. The API writes it;
the worker only reads it (its data mount is read-only).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID


def _path(data_dir: Path, job_id: UUID | str) -> Path:
    return data_dir / "jobs" / str(job_id) / "mapping_overrides.json"


def load_overrides(data_dir: Path, job_id: UUID | str) -> dict[str, str]:
    path = _path(data_dir, job_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def save_overrides(data_dir: Path, job_id: UUID | str, overrides: dict[str, str]) -> None:
    """Merge `overrides` into the stored ones; atomic write."""
    merged = {**load_overrides(data_dir, job_id), **overrides}
    path = _path(data_dir, job_id)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
