"""Streaming upload receiver: size-limited, content-checked, atomic.

The body is read in chunks, hashed and written to `<name>.part`. We stop as soon as the byte
count passes the limit or the first bytes do not match the extension, so a bad upload never
costs more than it has to. Only a fully checked file is renamed into place.
"""

import hashlib
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from c2b_api.magic import HEAD_BYTES, TypeSpec, UploadRefused, check_complete, check_head, spec_for

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MB = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    display_name: str
    spec: TypeSpec
    size_bytes: int
    sha256: str
    path: Path


def clean_filename(raw: str) -> str:
    """Display name only. The stored name is generated, so this never touches the filesystem."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL.sub("", name).strip()
    if not name or name in {".", ".."}:
        raise UploadRefused(422, "invalid_request", "File name is missing", "The file has no name.")
    return name[:255]


def limit_for(spec: TypeSpec, max_cad: int, max_other: int) -> int:
    return max_cad if spec.kind == "cad" else max_other


def too_large(name: str, spec: TypeSpec, size: int | None, limit: int) -> UploadRefused:
    who = "CAD files" if spec.kind == "cad" else "this kind of file"
    shown = f"`{name}` is {size / MB:.1f} MB; " if size else f"`{name}` is over the size limit; "
    return UploadRefused(
        413,
        "file_too_large",
        "File is too large",
        f"{shown}the limit for {who} is {limit // MB} MB.",
    )


async def receive(
    stream: AsyncIterator[bytes],
    *,
    filename: str,
    content_length: int | None,
    dest_dir: Path,
    file_stem: str,
    max_cad: int,
    max_other: int,
) -> StoredFile:
    display = clean_filename(filename)
    spec = spec_for(display)
    limit = limit_for(spec, max_cad, max_other)
    if content_length is not None and content_length > limit:
        raise too_large(display, spec, content_length, limit)

    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{file_stem}{Path(display).suffix.lower()}"
    part = dest_dir / f"{file_stem}.part"

    digest = hashlib.sha256()
    size = 0
    head = b""
    head_checked = False
    try:
        with open(part, "wb") as out:
            async for chunk in stream:
                size += len(chunk)
                if size > limit:
                    raise too_large(display, spec, None, limit)
                if not head_checked:
                    head += chunk[: max(0, HEAD_BYTES - len(head))]
                    if len(head) >= HEAD_BYTES:
                        check_head(display, spec, head)
                        head_checked = True
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadRefused(
                422, "invalid_request", "File is empty", f"`{display}` is empty (0 bytes)."
            )
        if not head_checked:
            check_head(display, spec, head)
        check_complete(display, spec, part)
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return StoredFile(display, spec, size, digest.hexdigest(), final)
