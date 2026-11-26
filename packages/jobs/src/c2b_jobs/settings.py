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
    library_dir: Path
    # Versioned library store (ADR-019): <data>/library/{versions,drafts,active}
    library_store_dir: Path
    # Native conversion through APS (ADR-018). Off unless configured.
    aps_client_id: str
    aps_client_secret: str
    aps_mode: str  # live | replay | record
    aps_fixture_dir: Path
    aps_monthly_cap: int
    aps_timeout_s: int
    # Admin (ADR-020). Disabled unless a passcode is set.
    admin_passcode: str
    admin_secret: str
    admin_session_s: int
    metrics_token: str
    # Retention, rate limits, environment (ADR-021, ADR-022)
    retention_days: int
    retention_sweep_s: int
    environment: str
    rate_job_create_per_min: int
    rate_upload_per_min: int
    rate_general_per_min: int
    rate_admin_login_per_min: int

    @property
    def aps_configured(self) -> bool:
        """Native conversion is available: credentials set, or a replay/record fixture run."""
        if self.aps_mode == "replay":
            return True
        return bool(self.aps_client_id and self.aps_client_secret)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_passcode)

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def converted_dir(self) -> Path:
        return self.data_dir / "converted"

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
        library_dir=Path(os.environ.get("C2B_LIBRARY_DIR", "library")),
        library_store_dir=Path(
            os.environ.get("C2B_LIBRARY_STORE_DIR")
            or Path(os.environ.get("C2B_DATA_DIR", "/data")) / "library"
        ),
        aps_client_id=os.environ.get("APS_CLIENT_ID", ""),
        aps_client_secret=os.environ.get("APS_CLIENT_SECRET", ""),
        aps_mode=os.environ.get("C2B_APS_MODE", "live"),
        aps_fixture_dir=Path(os.environ.get("C2B_APS_FIXTURE_DIR", "golden/B7_native/aps")),
        aps_monthly_cap=_int_env("C2B_APS_MONTHLY_CAP", 20),
        aps_timeout_s=_int_env("C2B_APS_TIMEOUT_S", 600),
        admin_passcode=os.environ.get("C2B_ADMIN_PASSCODE", ""),
        admin_secret=os.environ.get("C2B_ADMIN_SECRET", ""),
        admin_session_s=_int_env("C2B_ADMIN_SESSION_S", 3600),
        metrics_token=os.environ.get("C2B_METRICS_TOKEN", ""),
        retention_days=_int_env("C2B_RETENTION_DAYS", 30),
        retention_sweep_s=_int_env("C2B_RETENTION_SWEEP_S", 3600),
        environment=os.environ.get("C2B_ENV", "development"),
        rate_job_create_per_min=_int_env("C2B_RATE_JOB_CREATE_PER_MIN", 20),
        rate_upload_per_min=_int_env("C2B_RATE_UPLOAD_PER_MIN", 120),
        rate_general_per_min=_int_env("C2B_RATE_GENERAL_PER_MIN", 600),
        rate_admin_login_per_min=_int_env("C2B_RATE_ADMIN_LOGIN_PER_MIN", 5),
    )
