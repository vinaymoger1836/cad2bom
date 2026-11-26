"""Autodesk Platform Services adapter (ADR-018).

Flow, per file: 2-legged token -> transient bucket -> signed upload -> translate -> poll ->
download -> delete (always, in `finally`).

    .f3d            STEP (AP242) job                          -> 1 translation job
    .sldprt/.sldasm SVF2 job, then an OBJ job on its model     -> 2 translation jobs
                    guid, plus the object tree and properties

What is verified against Autodesk's docs: the token endpoint, bucket creation with the
`transient` policy (24 h retention), the translation job body for step/svf2/obj, and the
supported-translations table (SolidWorks has no STEP output). What is not: the signed-S3 upload
page could not be read, so that flow follows Autodesk's published OSS v2 API from memory, and
SolidWorks properties through SVF2 are expected but unconfirmed against a real file. Both are
flagged in the phase report.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from c2b_native_adapter.interface import (
    ConversionResult,
    NodeProps,
    UsageGate,
)

log = logging.getLogger("c2b.aps")

BASE = "https://developer.api.autodesk.com"
SCOPES = "bucket:create bucket:read data:read data:write data:create"
PART_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024


class ApsError(Exception):
    """A failure with a message that is safe and useful to show the person."""


def bucket_name(client_id: str) -> str:
    """Bucket keys are global across APS, so ours is derived from the client id."""
    return "c2b-" + hashlib.sha1(client_id.encode(), usedforsecurity=False).hexdigest()[:16]


def object_name(sha256: str, suffix: str) -> str:
    return f"{sha256[:24]}{suffix.lower()}"


def urn_for(bucket: str, obj: str) -> str:
    object_id = f"urn:adsk.objects:os.object:{bucket}/{obj}"
    return base64.urlsafe_b64encode(object_id.encode()).decode().rstrip("=")


def download_path(urn: str, derivative_urn: str) -> str:
    """Derivative URNs contain `:` and `/`, which APS wants percent-encoded in the path."""
    return f"/modelderivative/v2/designdata/{urn}/manifest/{quote(derivative_urn, safe='')}"


def jobs_for(suffix: str) -> int:
    return 1 if suffix.lower() == ".f3d" else 2


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


async def _file_part(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    with path.open("rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            block = f.read(min(1024 * 1024, left))
            if not block:
                break
            left -= len(block)
            yield block


class ApsAdapter:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: int = 600,
        poll_start_s: float = 2.0,
        poll_max_s: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_s = timeout_s
        self.poll_start_s = poll_start_s
        self.poll_max_s = poll_max_s
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=BASE,
            transport=transport,
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        )
        self._token: str | None = None
        self._token_expiry = 0.0
        self.bucket = bucket_name(client_id)

    # --- public API ----------------------------------------------------------------------

    def jobs_needed(self, file_path: Path) -> int:
        return jobs_for(file_path.suffix)

    async def convert(self, file_path: Path, out_dir: Path, gate: UsageGate) -> ConversionResult:
        suffix = file_path.suffix.lower()
        fmt = suffix.lstrip(".")
        needed = jobs_for(suffix)
        if not await gate.reserve(needed):
            return ConversionResult(
                status="cap_reached",
                source_format=fmt,
                message=(
                    "The monthly limit for native conversions on this server has been reached. "
                    "Please export your model as STEP (AP242 if you can) and upload that instead."
                ),
            )
        obj_name = object_name(_sha256(file_path), suffix)
        urn = urn_for(self.bucket, obj_name)
        uploaded = False
        counted = needed
        try:
            await self._ensure_bucket()
            await self._upload(file_path, obj_name)
            uploaded = True
            out_dir.mkdir(parents=True, exist_ok=True)
            if suffix == ".f3d":
                result = await self._convert_f3d(urn, out_dir, file_path.stem)
            else:
                result = await self._convert_solidworks(urn, out_dir, file_path.stem)
            return ConversionResult(
                status="ok",
                source_format=fmt,
                step_path=result.get("step"),
                obj_paths=result.get("obj", []),
                properties=result.get("props", []),
                usage_jobs=counted,
                notes=result.get("notes", []),
            )
        except ApsError as exc:
            if not uploaded:
                # Nothing reached the translation service, so the count is given back.
                await gate.refund(needed)
                counted = 0
            return ConversionResult(
                status="error", source_format=fmt, message=str(exc), usage_jobs=counted
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.warning("APS transport error: %s", type(exc).__name__)
            if not uploaded:
                await gate.refund(needed)
                counted = 0
            return ConversionResult(
                status="error",
                source_format=fmt,
                message="Autodesk could not be reached or took too long. Please try again later, "
                "or export your model as STEP and upload that.",
                usage_jobs=counted,
            )
        except Exception:
            # Never let an unexpected failure escape with the cap still counted or a half-done file
            # at Autodesk: report it, give the count back if nothing reached the translator.
            log.exception("unexpected error during native conversion")
            if not uploaded:
                await gate.refund(needed)
                counted = 0
            return ConversionResult(
                status="error",
                source_format=fmt,
                message="The conversion failed unexpectedly. Please try again, or export your "
                "model as STEP and upload that.",
                usage_jobs=counted,
            )
        finally:
            await self._cleanup(urn, obj_name)
            await self._http.aclose()

    # --- steps ---------------------------------------------------------------------------

    async def _auth(self) -> dict[str, str]:
        if self._token is None or time.monotonic() > self._token_expiry - 30:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            r = await self._http.post(
                "/authentication/v2/token",
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "client_credentials", "scope": SCOPES},
            )
            if r.status_code in (400, 401, 403):
                raise ApsError(
                    "Autodesk did not accept this server's conversion credentials. "
                    "Ask the administrator to check them."
                )
            self._check(r, "sign in")
            body = r.json()
            self._token = str(body["access_token"])
            self._token_expiry = time.monotonic() + float(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _check(self, r: httpx.Response, what: str) -> None:
        if r.status_code == 429:
            raise ApsError("Autodesk is busy right now. Please try again in a few minutes.")
        if r.status_code >= 500:
            raise ApsError(
                f"Autodesk had a problem while trying to {what}. Please try again later."
            )
        if r.status_code >= 400:
            raise ApsError(
                f"Autodesk refused the request to {what} (status {r.status_code}). "
                "Please export your model as STEP and upload that instead."
            )

    async def _ensure_bucket(self) -> None:
        r = await self._http.post(
            "/oss/v2/buckets",
            headers=await self._auth(),
            json={"bucketKey": self.bucket, "policyKey": "transient"},
        )
        if r.status_code == 409:  # already ours
            return
        self._check(r, "prepare storage")

    async def _upload(self, path: Path, obj: str) -> None:
        size = path.stat().st_size
        parts = max(1, math.ceil(size / PART_BYTES))
        base = f"/oss/v2/buckets/{self.bucket}/objects/{obj}/signeds3upload"
        r = await self._http.get(
            base, params={"parts": parts, "firstPart": 1}, headers=await self._auth()
        )
        self._check(r, "upload your file")
        info = r.json()
        urls: list[str] = info["urls"]
        upload_key = info["uploadKey"]
        for i, url in enumerate(urls):
            start = i * PART_BYTES
            length = min(PART_BYTES, size - start)
            put = await self._http.put(
                url,
                content=_file_part(path, start, length),
                headers={"Content-Length": str(length)},
            )
            self._check(put, "upload your file")
        done = await self._http.post(
            base, json={"uploadKey": upload_key}, headers=await self._auth()
        )
        self._check(done, "finish the upload")

    async def _post_job(self, urn: str, formats: list[dict[str, Any]]) -> None:
        r = await self._http.post(
            "/modelderivative/v2/designdata/job",
            headers={**await self._auth(), "x-ads-force": "true"},
            json={"input": {"urn": urn}, "output": {"formats": formats}},
        )
        self._check(r, "start the conversion")

    async def _wait(self, urn: str, output_type: str) -> dict[str, Any]:
        """Poll the manifest until the derivative of `output_type` is finished."""
        deadline = time.monotonic() + self.timeout_s
        delay = self.poll_start_s
        while True:
            r = await self._http.get(
                f"/modelderivative/v2/designdata/{urn}/manifest", headers=await self._auth()
            )
            self._check(r, "check the conversion")
            manifest: dict[str, Any] = r.json()
            for d in manifest.get("derivatives", []):
                if d.get("outputType") != output_type:
                    continue
                status = d.get("status")
                if status == "success":
                    return manifest
                if status in ("failed", "timeout"):
                    raise ApsError(
                        "Autodesk could not convert this file. It may be damaged, saved by a "
                        "version Autodesk does not read yet, or have an unsupported structure. "
                        "Please export it as STEP and upload that instead."
                    )
            if manifest.get("status") in ("failed", "timeout"):
                raise ApsError("Autodesk could not convert this file. Please try a STEP export.")
            if time.monotonic() > deadline:
                raise ApsError(
                    "Autodesk did not finish converting in time. Please try again, "
                    "or export your model as STEP and upload that."
                )
            await self._sleep(delay)
            delay = min(delay * 1.5, self.poll_max_s)

    @staticmethod
    def _resources(manifest: dict[str, Any], output_type: str, ext: tuple[str, ...]) -> list[str]:
        """URNs of the finished files of one output type with one of the given extensions."""
        found: list[str] = []

        def walk(node: dict[str, Any]) -> None:
            urn = str(node.get("urn", ""))
            if node.get("type") == "resource" and urn.lower().endswith(ext):
                found.append(urn)
            for child in node.get("children", []) or []:
                walk(child)

        for d in manifest.get("derivatives", []):
            if d.get("outputType") == output_type:
                walk(d)
        return found

    async def _download(self, urn: str, derivative_urn: str, dest: Path) -> Path:
        r = await self._http.get(download_path(urn, derivative_urn), headers=await self._auth())
        self._check(r, "download the converted file")
        if len(r.content) > MAX_DOWNLOAD_BYTES:
            raise ApsError("The converted file is too large to download.")
        if not r.content:
            raise ApsError("Autodesk returned an empty file.")
        dest.write_bytes(r.content)
        return dest

    async def _convert_f3d(self, urn: str, out_dir: Path, stem: str) -> dict[str, Any]:
        await self._post_job(urn, [{"type": "step", "advanced": {"applicationProtocol": "242"}}])
        manifest = await self._wait(urn, "step")
        found = self._resources(manifest, "step", (".step", ".stp"))
        if not found:
            raise ApsError("Autodesk finished but returned no STEP file.")
        step = await self._download(urn, found[0], out_dir / f"{stem}.step")
        return {"step": step, "notes": ["Converted by Autodesk to STEP AP242."]}

    async def _convert_solidworks(self, urn: str, out_dir: Path, stem: str) -> dict[str, Any]:
        await self._post_job(urn, [{"type": "svf2", "views": ["3d"]}])
        await self._wait(urn, "svf2")
        guid = await self._model_guid(urn)
        tree = await self._get_json(f"/modelderivative/v2/designdata/{urn}/metadata/{guid}")
        props = await self._get_properties(urn, guid)
        await self._post_job(
            urn,
            [
                {
                    "type": "obj",
                    "advanced": {"modelGuid": guid, "objectIds": [-1], "unit": "millimeter"},
                }
            ],
        )
        manifest = await self._wait(urn, "obj")
        found = self._resources(manifest, "obj", (".obj",))
        if not found:
            raise ApsError("Autodesk finished but returned no mesh file.")
        objs = [
            await self._download(urn, u, out_dir / f"{stem}_{i}.obj") for i, u in enumerate(found)
        ]
        nodes = flatten_properties(tree, props)
        notes = [
            "Geometry comes from meshes, not exact solids: shape features have lower confidence."
        ]
        if not any(n.props for n in nodes):
            notes.append("Autodesk returned no part properties for this file.")
        return {"obj": objs, "props": nodes, "notes": notes}

    async def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        for attempt in range(6):
            r = await self._http.get(path, params=params, headers=await self._auth())
            if r.status_code == 202:  # still extracting
                await self._sleep(self.poll_start_s * (attempt + 1))
                continue
            self._check(r, "read the model data")
            data: dict[str, Any] = r.json()
            return data
        raise ApsError("Autodesk was still preparing the model data. Please try again later.")

    async def _model_guid(self, urn: str) -> str:
        meta = await self._get_json(f"/modelderivative/v2/designdata/{urn}/metadata")
        views = meta.get("data", {}).get("metadata", [])
        three_d = next((v for v in views if v.get("role") == "3d"), views[0] if views else None)
        if not three_d or "guid" not in three_d:
            raise ApsError("Autodesk found no 3D view in this file.")
        return str(three_d["guid"])

    async def _get_properties(self, urn: str, guid: str) -> dict[str, Any]:
        return await self._get_json(
            f"/modelderivative/v2/designdata/{urn}/metadata/{guid}/properties",
            params={"forceget": "true"},
        )

    async def _cleanup(self, urn: str, obj: str) -> None:
        """Delete our copy at Autodesk. Failures are logged; the bucket is transient anyway."""
        try:
            headers = await self._auth()
            await self._http.delete(
                f"/modelderivative/v2/designdata/{urn}/manifest", headers=headers
            )
            await self._http.delete(f"/oss/v2/buckets/{self.bucket}/objects/{obj}", headers=headers)
        except Exception as exc:
            log.warning(
                "APS cleanup failed (%s); the transient bucket expires it", type(exc).__name__
            )


def flatten_properties(tree: dict[str, Any], props: dict[str, Any]) -> list[NodeProps]:
    """Join the object tree (names and hierarchy) with the property collection (by objectid)."""
    paths: dict[int, tuple[str, str]] = {}

    def walk(nodes: list[dict[str, Any]], parent: str) -> None:
        for n in nodes:
            name = str(n.get("name", ""))
            path = f"{parent} / {name}" if parent else name
            if isinstance(n.get("objectid"), int):
                paths[int(n["objectid"])] = (name, path)
            walk(n.get("objects", []) or [], path)

    walk(tree.get("data", {}).get("objects", []), "")
    out: list[NodeProps] = []
    for item in props.get("data", {}).get("collection", []):
        oid = item.get("objectid")
        name, path = paths.get(oid, (str(item.get("name", "")), str(item.get("name", ""))))
        flat: dict[str, str] = {}
        for group, values in (item.get("properties") or {}).items():
            if not isinstance(values, dict):
                continue
            for key, val in values.items():
                if val is None or isinstance(val, dict | list):
                    continue
                text = str(val).strip()
                if not text:
                    continue
                flat[f"{group}.{key}"] = text
                flat.setdefault(str(key), text)
        out.append(NodeProps(name=name, path=path, props=flat))
    return out
