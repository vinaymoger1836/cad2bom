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
TIMEOUT_S = int(os.environ.get("C2B_CAD_TIMEOUT_S", "120"))
MAX_REQUEST_BYTES = 64 * 1024

_one_at_a_time = asyncio.Semaphore(1)


def _reply(status: str, message: str, **extra: object) -> bytes:
    return json.dumps({"status": status, "message": message, **extra}).encode() + b"\n"


def _safe_input_path(raw: object) -> Path | None:
    """Only files under <data>/jobs/<id>/in/ are readable; anything else is refused."""
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
    if len(rel.parts) < 4 or rel.parts[0] != "jobs" or rel.parts[2] != "in":
        return None
    return path


async def _run_child(path: Path) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "c2b_cad_worker.extract_stub",
        str(path),
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
        if not isinstance(req, dict) or req.get("op") != "extract":
            writer.write(_reply("error", "Unknown operation."))
            return
        path = _safe_input_path(req.get("path"))
        if path is None:
            writer.write(_reply("error", "That file is not available to the CAD reader."))
            return
        async with _one_at_a_time:
            writer.write(await _run_child(path))
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
