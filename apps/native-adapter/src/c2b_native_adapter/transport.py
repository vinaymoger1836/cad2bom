"""HTTP transports for the APS adapter: live, record and replay (ADR-018).

Replay is how B7 runs without credentials or network: every APS exchange is a JSON file in a
fixture directory, keyed by method + path (+ a hash of a JSON request body). Repeated requests to
the same key (polling a manifest) return the recorded responses in order, then the last one again.

Record mode wraps the live transport and writes the same files, with tokens and signed-URL query
strings removed. Recordings are git-ignored when made against real APS (`cassettes/aps-live/`).
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

REDACTED = "REDACTED"
_SECRET_KEYS = {"access_token", "client_secret", "uploadKey"}


def exchange_key(request: httpx.Request) -> str:
    """Stable id of a request: `METHOD /path`, plus the output types of a translation job
    (`POST /modelderivative/v2/designdata/job #step`), so STEP and OBJ requests stay apart."""
    path = urlsplit(str(request.url)).path
    key = f"{request.method} {path}"
    if request.method == "POST" and path.endswith("/designdata/job"):
        try:
            formats = json.loads(request.content)["output"]["formats"]
            key += " #" + ",".join(sorted(str(f.get("type")) for f in formats))
        except (ValueError, KeyError, TypeError, httpx.RequestNotRead):
            pass
    return key


def _file_name(key: str) -> str:
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16] + ".json"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k in _SECRET_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and value.startswith("http") and "?" in value:
        return value.split("?", 1)[0] + "?" + REDACTED
    return value


def _encode_body(content: bytes, content_type: str) -> dict[str, Any]:
    if "json" in content_type:
        try:
            return {"json": _redact(json.loads(content))}
        except ValueError:
            pass
    try:
        return {"text": content.decode("utf-8")}
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(content).decode()}


def _decode_body(rec: dict[str, Any], directory: Path | None = None) -> bytes:
    if "file" in rec and directory is not None:  # a body kept as its own file (e.g. a STEP)
        target = (directory / str(rec["file"])).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise FixtureError(f"fixture file {rec['file']!r} is outside the fixture directory")
        return target.read_bytes()
    if "json" in rec:
        return json.dumps(rec["json"]).encode()
    if "text" in rec:
        return str(rec["text"]).encode()
    if "b64" in rec:
        return base64.b64decode(rec["b64"])
    return b""


class FixtureError(RuntimeError):
    """A request had no recorded answer (replay mode)."""


class ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._served: dict[str, int] = {}
        self.requests: list[str] = []  # keys in the order they were asked (asserted by B7)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = exchange_key(request)
        self.requests.append(key)
        path = self.directory / _file_name(key)
        if not path.is_file():
            raise FixtureError(f"no recorded APS response for {key!r} in {self.directory}")
        data = json.loads(path.read_text(encoding="utf-8"))
        responses: list[dict[str, Any]] = data["responses"]
        n = self._served.get(key, 0)
        self._served[key] = n + 1
        rec = responses[min(n, len(responses) - 1)]
        headers = {"content-type": rec.get("content_type", "application/json")}
        return httpx.Response(
            rec["status"],
            headers=headers,
            content=_decode_body(rec, self.directory),
            request=request,
        )


class RecordTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.directory = directory
        self.inner = inner or httpx.AsyncHTTPTransport()
        directory.mkdir(parents=True, exist_ok=True)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = exchange_key(request)
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        ctype = response.headers.get("content-type", "application/octet-stream")
        rec = {"status": response.status_code, "content_type": ctype.split(";")[0]}
        rec.update(_encode_body(body, ctype))
        path = self.directory / _file_name(key)
        data: dict[str, Any] = {"key": key, "responses": []}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        data["responses"].append(rec)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return httpx.Response(
            response.status_code, headers=response.headers, content=body, request=request
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def write_fixture(directory: Path, key: str, responses: list[dict[str, Any]]) -> Path:
    """Write one fixture file (used by the fixture builder in tools/fixtures)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _file_name(key)
    path.write_text(
        json.dumps({"key": key, "responses": responses}, indent=2) + "\n", encoding="utf-8"
    )
    return path
