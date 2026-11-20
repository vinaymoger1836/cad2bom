"""Job access tokens (ADR-013): 32 random bytes, only the SHA-256 is stored.

SHA-256 (not a slow password hash) is right here because the token is 256 bits of randomness,
so there is nothing to brute-force; comparison is constant-time.
"""

import hashlib
import hmac
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from c2b_api.errors import unauthorized
from c2b_jobs import repo
from c2b_jobs.repo import Pool


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def require_job(
    request: Request,
    job_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Dependency: returns the job row, or 401 for a missing/wrong token or unknown job.

    Unknown job and wrong token deliberately look identical, so ids cannot be probed.
    """
    pool: Pool = request.app.state.pool
    token = _bearer(authorization)
    row = await repo.get_job(pool, job_id)
    expected = bytes(row["token_hash"]) if row else hashlib.sha256(b"decoy").digest()
    supplied = hash_token(token) if token else b"\x00" * 32
    if not hmac.compare_digest(expected, supplied) or row is None or token is None:
        raise unauthorized()
    return row


JobRow = Annotated[dict[str, object], Depends(require_job)]
