"""Unix-socket server for the sandboxed cad-worker (ADR-012).

Runs with no network, no database credentials and a read-only root filesystem. Each request is
handled in a child process that is killed when it exceeds the per-file time limit, so a hostile
or broken file can only cost one child process and `C2B_CAD_TIMEOUT_S` seconds.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("c2b.cad_worker")

SOCKET_PATH = Path(os.environ.get("C2B_CAD_SOCKET", "/run/c2b/cad.sock"))
DATA_ROOT = Path(os.environ.get("C2B_DATA_DIR", "/data")).resolve()
# Results go to a separate writable tree so the input mount can stay read-only: <out>/<job id>/.
OUT_ROOT = Path(os.environ.get("C2B_CAD_OUT_DIR", str(DATA_ROOT / "out")))
TIMEOUT_S = int(os.environ.get("C2B_CAD_TIMEOUT_S", "120"))
MAX_REQUEST_BYTES = 256 * 1024

_one_at_a_time = asyncio.Semaphore(1)


def _reply(status: str, message: str, **extra: object) -> bytes:
    return json.dumps({"status": status, "message": message, **extra}).encode() + b"\n"


def _safe_input_path(raw: object) -> Path | None:
    """Only files under <data>/jobs/<id>/in/ (uploads) or <data>/converted/<id>/ (files the
    native adapter made from an upload) are readable; anything else is refused."""
    if not isinstance(raw, str):
        return None
    try:
        path = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        rel = path.relative_to(DATA_ROOT)
    except ValueError:
        return None
    uploads = len(rel.parts) >= 4 and rel.parts[0] == "jobs" and rel.parts[2] == "in"
    converted = len(rel.parts) >= 3 and rel.parts[0] == "converted"
    if not (uploads or converted):
        return None
    return path


def _job_id(path: Path) -> str:
    return path.relative_to(DATA_ROOT).parts[1]


async def _run_child(path: Path, extra_paths: list[Path] | None = None, name: str = "") -> bytes:
    """Read one STEP file (`extract`) or a set of OBJ meshes (`mesh_extract`) in a child process."""
    out_dir = OUT_ROOT / _job_id(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    if extra_paths is None:
        argv = ["c2b_cad_worker.extract", str(path), str(out_dir)]
    else:
        argv = ["c2b_cad_worker.mesh", str(out_dir), name or path.stem, *map(str, extra_paths)]
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return _reply(
            "timeout",
            f"Reading this CAD file took longer than {TIMEOUT_S} seconds and was stopped. "
            "Try a simpler export or split the assembly.",
        )
    if proc.returncode != 0:
        log.warning(
            "extract child exited %s: %s", proc.returncode, err.decode(errors="replace")[:500]
        )
        return _reply(
            "error",
            "The CAD reader stopped unexpectedly while reading this file. "
            "It may be damaged; try exporting it again.",
            exit_code=proc.returncode,
        )
    return out.strip().splitlines()[-1] + b"\n" if out.strip() else _reply("error", "No answer.")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not line or len(line) > MAX_REQUEST_BYTES:
            writer.write(_reply("error", "Bad request."))
            return
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            writer.write(_reply("error", "Bad request."))
            return
        op = req.get("op") if isinstance(req, dict) else None
        if op not in {"extract", "mesh_extract"}:
            writer.write(_reply("error", "Unknown operation."))
            return
        if op == "extract":
            path = _safe_input_path(req.get("path"))
            if path is None:
                writer.write(_reply("error", "That file is not available to the CAD reader."))
                return
            async with _one_at_a_time:
                writer.write(await _run_child(path))
            return
        raw_paths = req.get("paths")
        if not isinstance(raw_paths, list) or not 0 < len(raw_paths) <= 64:
            writer.write(_reply("error", "That file is not available to the CAD reader."))
            return
        safe = [_safe_input_path(p) for p in raw_paths]
        if any(p is None for p in safe) or len({_job_id(p) for p in safe if p}) != 1:
            writer.write(_reply("error", "That file is not available to the CAD reader."))
            return
        meshes = [p for p in safe if p is not None]
        async with _one_at_a_time:
            writer.write(await _run_child(meshes[0], meshes, str(req.get("name") or "")[:120]))
    except Exception:
        log.exception("request failed")
        with contextlib.suppress(Exception):
            writer.write(_reply("error", "The CAD reader hit an internal error."))
    finally:
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()


async def _serve() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(_handle, path=str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    log.info("cad-worker listening on %s (limit %ss per file)", SOCKET_PATH, TIMEOUT_S)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
