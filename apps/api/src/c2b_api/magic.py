"""Content-based upload type checks (CLAUDE.md rule 8: type-checked by content, not by name).

An upload is accepted only when the *extension* and the *leading bytes* agree. Messages are
written for the person uploading, because they are shown verbatim in the UI.
"""

import codecs
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["cad", "photo", "bom_file", "datasheet"]

# Bytes needed before the signature check can run.
HEAD_BYTES = 4096


@dataclass(frozen=True)
class TypeSpec:
    detected: str  # stable id stored in the DB, e.g. "step"
    kind: Kind
    label: str  # human name used in messages
    expected_family: str  # which sniffed family satisfies this spec


# extension -> spec
ACCEPTED: dict[str, TypeSpec] = {
    ".step": TypeSpec("step", "cad", "STEP", "step"),
    ".stp": TypeSpec("step", "cad", "STEP", "step"),
    ".jpg": TypeSpec("jpeg", "photo", "JPEG photo", "jpeg"),
    ".jpeg": TypeSpec("jpeg", "photo", "JPEG photo", "jpeg"),
    ".png": TypeSpec("png", "photo", "PNG photo", "png"),
    ".webp": TypeSpec("webp", "photo", "WebP photo", "webp"),
    ".pdf": TypeSpec("pdf", "datasheet", "PDF", "pdf"),
    ".xlsx": TypeSpec("xlsx", "bom_file", "Excel (.xlsx)", "zip"),
    ".xls": TypeSpec("xls", "bom_file", "Excel (.xls)", "ole"),
    ".csv": TypeSpec("csv", "bom_file", "CSV", "text"),
}

# Native CAD formats are recognised only to give a helpful refusal (consent flow arrives later).
NATIVE_CAD = {".sldprt", ".sldasm", ".f3d"}

ACCEPTED_EXTENSIONS = sorted(ACCEPTED)


class UploadRefused(Exception):
    """Raised with a status, a machine code and a message meant for the person."""

    def __init__(self, status: int, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def spec_for(filename: str) -> TypeSpec:
    ext = Path(filename).suffix.lower()
    if ext in NATIVE_CAD:
        raise UploadRefused(
            415,
            "unsupported_type",
            "Native CAD files are not supported yet",
            f"`{filename}` is a native CAD file. Converting SolidWorks or Fusion files is not "
            "available yet. Please export it as STEP (AP242 if you can) and upload that instead.",
        )
    spec = ACCEPTED.get(ext)
    if spec is None:
        shown = ext or "(no extension)"
        raise UploadRefused(
            415,
            "unsupported_type",
            "This file type is not accepted",
            f"`{filename}` has type {shown}, which is not accepted. "
            f"Accepted types: {', '.join(ACCEPTED_EXTENSIONS)}.",
        )
    return spec


def sniff(head: bytes) -> tuple[str, str]:
    """Return (family, human description) from the leading bytes."""
    stripped = head.lstrip(codecs.BOM_UTF8).lstrip(b" \t\r\n")
    if stripped.startswith(b"ISO-10303-21;"):
        return "step", "a STEP file"
    if head.startswith(b"MZ"):
        return "exe", "a Windows program"
    if head.startswith(b"\x7fELF"):
        return "elf", "a Linux program"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg", "a JPEG image"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "a PNG image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "a WebP image"
    if b"%PDF-" in head[:1024]:
        return "pdf", "a PDF document"
    if head.startswith(b"PK\x03\x04"):
        return "zip", "a ZIP archive (this includes .xlsx and other Office files)"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole", "an old-format Office document"
    if _looks_like_text(head):
        return "text", "plain text"
    return "binary", "unrecognised binary data"


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        text = head.decode("latin-1")
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\r\n\f")
    return controls <= max(1, len(text) // 50)


def check_head(filename: str, spec: TypeSpec, head: bytes) -> None:
    """Refuse when the bytes do not match what the extension promises."""
    family, described = sniff(head)
    if family == spec.expected_family:
        return
    # A STEP file is also "text"; for .csv we accept text, so only the reverse case needs care.
    if spec.expected_family == "text" and family == "step":
        return
    starts = _starts_with(head)
    if spec.expected_family == "step":
        detail = (
            f"`{filename}` is not a valid STEP file: it is {described}"
            + (f" (it starts with `{starts}`)" if starts else "")
            + ", not text that begins with `ISO-10303-21;`."
        )
    else:
        detail = (
            f"`{filename}` is not a valid {spec.label} file: its content looks like "
            f"{described}. Check that the file was exported correctly and has the right extension."
        )
    raise UploadRefused(415, "type_mismatch", "File content does not match its type", detail)


def _starts_with(head: bytes) -> str:
    snippet = head[:2]
    if len(snippet) == 2 and all(32 <= b < 127 for b in snippet):
        return snippet.decode("ascii")
    return ""


def check_complete(filename: str, spec: TypeSpec, path: Path) -> None:
    """Checks that need the whole file (run after upload, before it is accepted)."""
    if spec.detected == "xlsx":
        ok = False
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            ok = "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)
        if not ok:
            raise UploadRefused(
                415,
                "type_mismatch",
                "File content does not match its type",
                f"`{filename}` is a ZIP archive but not an Excel workbook (.xlsx).",
            )
