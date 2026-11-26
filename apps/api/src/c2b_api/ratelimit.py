"""Per-IP rate limits (ADR-022): token buckets in memory, as a plain ASGI middleware.

In memory is enough for the single API replica this stack runs; a second replica would need a
shared store (documented in the phase report). The client IP comes from the right-most
`X-Forwarded-For` entry, which is the one Caddy appended; it is never trusted from the client.
"""

import json
import time
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from c2b_jobs.settings import get_settings

STATS = {"limited_total": 0}


@dataclass
class _Bucket:
    tokens: float
    updated: float


class Limiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def allow(self, name: str, ip: str, per_min: int) -> float:
        """0.0 if allowed, else the seconds to wait. `per_min <= 0` disables the limit."""
        if per_min <= 0:
            return 0.0
        now = time.monotonic()
        rate = per_min / 60.0
        key = (name, ip)
        b = self._buckets.get(key)
        if b is None:
            if len(self._buckets) > 20000:
                self._prune(now)
            b = self._buckets[key] = _Bucket(float(per_min), now)
        b.tokens = min(float(per_min), b.tokens + (now - b.updated) * rate)
        b.updated = now
        if b.tokens >= 1.0:
            b.tokens -= 1.0
            return 0.0
        return (1.0 - b.tokens) / rate

    def _prune(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > 600]
        for k in stale:
            del self._buckets[k]


def client_ip(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            parts = [p.strip() for p in value.decode("latin-1").split(",") if p.strip()]
            if parts:
                return parts[-1]
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


def classify(method: str, path: str) -> str | None:
    """Which bucket a request counts against (None = not limited)."""
    if path in {"/healthz", "/readyz"}:
        return None
    if path == "/admin/login":
        return "admin_login"
    if method == "POST" and path == "/jobs":
        return "job_create"
    if method == "POST" and path.startswith("/jobs/") and path.endswith("/files"):
        return "upload"
    if path.endswith("/events") or path == "/demo/stream":
        return "stream"
    return "general"


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limiter = Limiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        s = get_settings()
        limits = {
            "admin_login": s.rate_admin_login_per_min,
            "job_create": s.rate_job_create_per_min,
            "upload": s.rate_upload_per_min,
            "stream": s.rate_general_per_min,
            "general": s.rate_general_per_min,
        }
        bucket = classify(scope["method"], scope["path"])
        if bucket is not None:
            wait = self.limiter.allow(bucket, client_ip(scope), limits[bucket])
            if wait > 0:
                STATS["limited_total"] += 1
                retry = max(1, int(wait + 0.999))
                body = json.dumps(
                    {
                        "type": "about:blank",
                        "title": "Too many requests",
                        "status": 429,
                        "detail": f"Too many requests. Please wait {retry} seconds and try again.",
                        "code": "rate_limited",
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [
                            (b"content-type", b"application/problem+json"),
                            (b"retry-after", str(retry).encode()),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
