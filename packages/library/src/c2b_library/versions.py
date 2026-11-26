"""Versioned library store (ADR-019).

Layout under the store root (`<data>/library/`):

    versions/<version>/...   immutable snapshots, one per published version
    drafts/<id>/...          editable copies, never read by jobs
    active                   one line: the version new jobs are pinned to

The baseline library baked into the image (`library/`) is seeded as the first version. A job is
pinned to a version string at creation and always resolves it through `resolve_library`, so
publishing a new version never changes an existing job.
"""

from __future__ import annotations

import difflib
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from c2b_library.loader import Library, library_checksum, load_library

VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d+$")
DRAFT_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_FILE_BYTES = 512 * 1024


class VersionError(RuntimeError):
    """A version or draft could not be resolved or written."""


@dataclass(frozen=True)
class VersionInfo:
    version: str
    released: str
    changelog: list[str]
    checksum: str
    file_count: int
    active: bool


def _versions_dir(store: Path) -> Path:
    return store / "versions"


def _drafts_dir(store: Path) -> Path:
    return store / "drafts"


def _read_version_file(root: Path) -> dict[str, object]:
    data = yaml.safe_load((root / "VERSION.yaml").read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def baseline_version(baseline_dir: Path) -> str:
    return str(_read_version_file(baseline_dir).get("version", "none"))


def seed(store: Path, baseline_dir: Path) -> str:
    """Copy the image's library in as a version (once) and make it active if none is."""
    version = baseline_version(baseline_dir)
    if not VERSION_RE.match(version):
        raise VersionError(f"baseline library has no valid version ({version!r})")
    target = _versions_dir(store) / version
    if not target.exists():
        _versions_dir(store).mkdir(parents=True, exist_ok=True)
        tmp = _versions_dir(store) / f".{version}.{uuid.uuid4().hex[:6]}"
        shutil.copytree(baseline_dir, tmp, ignore=shutil.ignore_patterns("__pycache__"))
        tmp.rename(target)
    if not (store / "active").is_file():
        set_active(store, version)
    return version


def set_active(store: Path, version: str) -> None:
    if not (_versions_dir(store) / version).is_dir():
        raise VersionError(f"version {version} does not exist")
    store.mkdir(parents=True, exist_ok=True)
    tmp = store / ".active.tmp"
    tmp.write_text(version + "\n", encoding="utf-8")
    tmp.replace(store / "active")  # atomic on POSIX and Windows


def active_version(store: Path, baseline_dir: Path) -> str:
    """The version a new job is pinned to. Falls back to the baseline before seeding."""
    try:
        v = (store / "active").read_text(encoding="utf-8").strip()
    except OSError:
        v = ""
    return v if VERSION_RE.match(v) else baseline_version(baseline_dir)


def version_path(store: Path, baseline_dir: Path, version: str) -> Path:
    if not VERSION_RE.match(version):
        raise VersionError(f"not a library version: {version!r}")
    p = _versions_dir(store) / version
    if p.is_dir():
        return p
    if baseline_version(baseline_dir) == version:
        return baseline_dir  # image copy, before the store has been seeded
    raise VersionError(f"library version {version} is not available on this server")


_CACHE: dict[tuple[str, str], Library] = {}


def resolve_library(store: Path, baseline_dir: Path, version: str) -> Library:
    """Load the library a job is pinned to. Versions are immutable, so loads are cached."""
    path = version_path(store, baseline_dir, version)
    key = (str(path), version)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    lib, issues = load_library(path)
    if lib is None:
        first = issues[0] if issues else None
        raise VersionError(f"library {version} could not be loaded: {first}")
    _CACHE[key] = lib
    return lib


def list_versions(store: Path, baseline_dir: Path) -> list[VersionInfo]:
    active = active_version(store, baseline_dir)
    out: list[VersionInfo] = []
    root = _versions_dir(store)
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []
    if not dirs:
        dirs = [baseline_dir]
    for p in dirs:
        meta = _read_version_file(p)
        checksum, count = library_checksum(p)
        cl = meta.get("changelog", [])
        out.append(
            VersionInfo(
                version=str(meta.get("version", p.name)),
                released=str(meta.get("date", "")),
                changelog=[str(c) for c in cl] if isinstance(cl, list) else [str(cl)],
                checksum=checksum,
                file_count=count,
                active=str(meta.get("version", p.name)) == active,
            )
        )
    return sorted(out, key=lambda v: _version_key(v.version), reverse=True)


def _version_key(v: str) -> tuple[int, int, int]:
    try:
        y, m, n = v.split(".")
        return int(y), int(m), int(n)
    except ValueError:
        return (0, 0, 0)


def next_version(store: Path, baseline_dir: Path, today: date | None = None) -> str:
    today = today or date.today()
    prefix = f"{today.year:04d}.{today.month:02d}."
    latest = max(
        (_version_key(v.version) for v in list_versions(store, baseline_dir)),
        default=(0, 0, 0),
    )
    n = latest[2] + 1 if (latest[0], latest[1]) == (today.year, today.month) else 0
    return f"{prefix}{n}"


# --- files ---------------------------------------------------------------------------------


def _safe(root: Path, rel: str) -> Path:
    """Resolve a relative library path; only `*.yaml` below the root (no traversal)."""
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()) or p.suffix != ".yaml" or ".." in Path(rel).parts:
        raise VersionError(f"not a library file: {rel!r}")
    return p


def list_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.yaml") if p.is_file())


def read_file(root: Path, rel: str) -> str:
    p = _safe(root, rel)
    if not p.is_file():
        raise VersionError(f"no such file: {rel}")
    return p.read_text(encoding="utf-8")


# --- drafts --------------------------------------------------------------------------------


def create_draft(store: Path, baseline_dir: Path, from_version: str) -> str:
    src = version_path(store, baseline_dir, from_version)
    draft_id = uuid.uuid4().hex[:12]
    dest = _drafts_dir(store) / draft_id
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".base").write_text(from_version + "\n", encoding="utf-8")
    return draft_id


def draft_path(store: Path, draft_id: str) -> Path:
    if not DRAFT_RE.match(draft_id):
        raise VersionError("not a draft id")
    p = _drafts_dir(store) / draft_id
    if not p.is_dir():
        raise VersionError("draft not found")
    return p


def draft_base(store: Path, draft_id: str) -> str:
    f = draft_path(store, draft_id) / ".base"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def list_drafts(store: Path) -> list[dict[str, str]]:
    root = _drafts_dir(store)
    if not root.is_dir():
        return []
    return [
        {"id": p.name, "base": draft_base(store, p.name)}
        for p in sorted(root.iterdir())
        if p.is_dir() and DRAFT_RE.match(p.name)
    ]


def write_draft_file(store: Path, draft_id: str, rel: str, text: str) -> None:
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise VersionError("file is too large")
    root = draft_path(store, draft_id)
    p = _safe(root, rel)
    if not p.is_file():
        raise VersionError(f"no such file: {rel} (new files are not supported yet)")
    if rel == "VERSION.yaml":
        raise VersionError("VERSION.yaml is set when the draft is published")
    try:
        yaml.safe_load(text)  # YAML errors are reported to the editor before anything is written
    except yaml.YAMLError as exc:
        raise VersionError(f"not valid YAML: {exc}") from exc
    p.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def delete_draft(store: Path, draft_id: str) -> None:
    shutil.rmtree(draft_path(store, draft_id))


def validate_draft(store: Path, draft_id: str) -> tuple[bool, list[dict[str, str]]]:
    """Schema-validate and cross-check the draft with the same code as `c2b-library validate`."""
    from c2b_library.checks import run_checks

    root = draft_path(store, draft_id)
    lib, issues = load_library(root)
    if lib is not None:
        issues = list(issues) + list(run_checks(lib))
    items = [
        {"level": i.level, "code": i.code, "file": i.file, "message": i.message} for i in issues
    ]
    ok = lib is not None and not any(i.level == "error" for i in issues)
    return ok, items


def publish_draft(store: Path, baseline_dir: Path, draft_id: str, changelog: list[str]) -> str:
    """Freeze the draft as the next version and make it the active one. Refuses invalid drafts."""
    ok, _ = validate_draft(store, draft_id)
    if not ok:
        raise VersionError("the draft has validation errors; fix them before publishing")
    changelog = [c.strip() for c in changelog if c.strip()]
    if not changelog:
        raise VersionError("a changelog entry is required")
    root = draft_path(store, draft_id)
    version = next_version(store, baseline_dir)
    meta = _read_version_file(root)
    meta["version"] = version
    meta["date"] = date.today().isoformat()
    meta["changelog"] = changelog
    (root / "VERSION.yaml").write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n"
    )
    (root / ".base").unlink(missing_ok=True)
    target = _versions_dir(store) / version
    if target.exists():
        raise VersionError(f"version {version} already exists")
    _versions_dir(store).mkdir(parents=True, exist_ok=True)
    root.rename(target)
    set_active(store, version)
    return version


# --- diff ----------------------------------------------------------------------------------


def diff_versions(a: Path, b: Path, context: int = 2) -> list[dict[str, object]]:
    """Per-file unified diffs between two library directories (changed files only)."""
    out: list[dict[str, object]] = []
    for rel in sorted(set(list_files(a)) | set(list_files(b))):
        ta = (a / rel).read_text(encoding="utf-8").splitlines() if (a / rel).is_file() else []
        tb = (b / rel).read_text(encoding="utf-8").splitlines() if (b / rel).is_file() else []
        if ta == tb:
            continue
        if rel == "VERSION.yaml":
            continue
        lines = list(difflib.unified_diff(ta, tb, f"a/{rel}", f"b/{rel}", n=context, lineterm=""))
        added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
        status = "added" if not ta else "removed" if not tb else "changed"
        out.append(
            {"file": rel, "status": status, "added": added, "removed": removed, "diff": lines}
        )
    return out
