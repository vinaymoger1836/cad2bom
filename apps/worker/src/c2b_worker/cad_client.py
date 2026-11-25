"""Client for the sandboxed cad-worker, spoken over a Unix socket (ADR-012).

Protocol: one JSON object per line. Request `{"op": "extract", "path": "..."}`, response one
JSON object. The cad-worker has no network and no database credentials; this socket is its
only way in or out.
"""

import asyncio
import json
from pathlib import Path
from typing import Any


class CadWorkerError(Exception):
    """The cad-worker could not be reached or gave an unusable answer."""


# A large assembly's node tree is one JSON line; asyncio's default 64 KiB line limit is far too
# small. The cad-worker is our own sandboxed process, so this only bounds memory, not trust.
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024


async def extract(socket_path: Path, file_path: str, timeout_s: float) -> dict[str, Any]:
    # The cad-worker enforces its own per-file limit; we wait a little longer than that.
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path), limit=_MAX_RESPONSE_BYTES), timeout=10
        )
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as exc:
        raise CadWorkerError(
            "The CAD reader is not running right now. It will be retried when you start again."
        ) from exc
    try:
        writer.write(json.dumps({"op": "extract", "path": file_path}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout_s + 30)
    except TimeoutError as exc:
        raise CadWorkerError("The CAD reader did not answer in time.") from exc
    finally:
        writer.close()
    if not line:
        raise CadWorkerError("The CAD reader closed the connection without an answer.")
    try:
        result = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CadWorkerError("The CAD reader sent an answer that could not be understood.") from exc
    if not isinstance(result, dict):
        raise CadWorkerError("The CAD reader sent an answer that could not be understood.")
    return result
