"""Native CAD conversion, API side: consent text and the upload/start gates (ADR-018).

The conversion itself runs in the worker (`native.convert` task); the API only decides whether a
native file may be stored and whether a job may start.
"""

from pathlib import Path
from uuid import UUID

from c2b_api.errors import ProblemError
from c2b_api.magic import NATIVE_EXTENSIONS
from c2b_jobs import repo
from c2b_jobs.repo import Pool

# The exact words the person agrees to. Bump the version when the text changes; the version is
# stored with the job's consent so the record shows which wording was shown.
CONSENT_TEXT = "This file will be sent to Autodesk for conversion. Continue?"
CONSENT_TEXT_VERSION = "1"

CONSENT_REQUIRED = (
    "Converting a SolidWorks or Fusion file sends it to Autodesk. Confirm that on the upload page "
    "first, or export your model as STEP and upload that instead."
)


def is_native_name(filename: str) -> bool:
    return Path(filename).suffix.lower() in NATIVE_EXTENSIONS


def require_consent(job: dict[str, object]) -> None:
    if not job.get("consent_native_conversion"):
        raise ProblemError(409, "consent_required", "Consent needed", CONSENT_REQUIRED)


async def check_start(pool: Pool, job_id: UUID, job: dict[str, object]) -> None:
    """A job with a native file cannot start without recorded consent."""
    for u in await repo.list_uploads(pool, job_id):
        if u.detected_type in repo.NATIVE_TYPES:
            require_consent(job)
            return
