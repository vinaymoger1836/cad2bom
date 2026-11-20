"""Settings read from the environment. No secrets have defaults; see .env.example."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MB = 1024 * 1024


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    max_cad_bytes: int
    max_other_bytes: int
    max_files_per_job: int
    cad_socket: Path
    cad_timeout_s: int
    library_version: str

    @property
    def max_cad_mb(self) -> int:
        return self.max_cad_bytes // MB

    @property
    def max_other_mb(self) -> int:
        return self.max_other_bytes // MB


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    url = os.environ.get("C2B_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "C2B_DATABASE_URL is not set (see .env.example and infra/docker-compose.yml)"
        )
    return Settings(
        database_url=url,
        data_dir=Path(os.environ.get("C2B_DATA_DIR", "/data")),
        max_cad_bytes=_int_env("C2B_MAX_CAD_MB", 200) * MB,
        max_other_bytes=_int_env("C2B_MAX_OTHER_MB", 25) * MB,
        max_files_per_job=_int_env("C2B_MAX_FILES_PER_JOB", 50),
        cad_socket=Path(os.environ.get("C2B_CAD_SOCKET", "/run/c2b/cad.sock")),
        cad_timeout_s=_int_env("C2B_CAD_TIMEOUT_S", 120),
        # Pinned per job from P1 on (library loader). Placeholder until the library exists.
        library_version=os.environ.get("C2B_LIBRARY_VERSION", "none"),
    )
