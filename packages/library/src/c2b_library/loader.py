"""Read `library/` into validated models.

YAML is parsed with `safe_load` only (CLAUDE.md rule 12).
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from c2b_library.schemas import (
    Archetype,
    ConfidenceFile,
    HeadersFile,
    MaterialsFile,
    Profile,
    RuleFile,
    StandardTable,
    SynonymFile,
    Taxonomy,
    UnitsFile,
    VersionFile,
)

M = TypeVar("M", bound=BaseModel)

Level = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Issue:
    level: Level
    code: str
    file: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.file}: {self.message} ({self.code})"


@dataclass
class Library:
    root: Path
    version: VersionFile
    taxonomy: Taxonomy
    materials: MaterialsFile
    units: UnitsFile
    headers: HeadersFile
    confidence: ConfidenceFile
    profiles: dict[str, Profile]
    synonyms: dict[str, SynonymFile]  # keyed by relative path
    standards: dict[str, StandardTable]
    rules: dict[str, RuleFile]
    archetypes: dict[str, Archetype]
    checksum: str
    file_count: int
    issues: list[Issue] = field(default_factory=list)


def _read_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _fmt_errors(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        out.append(f"{loc}: {e['msg']}")
    return out


class _Loader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.issues: list[Issue] = []

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def parse(self, path: Path, model: type[M]) -> M | None:
        rel = self.rel(path)
        if not path.is_file():
            self.issues.append(Issue("error", "L-FILE-001", rel, "file is missing"))
            return None
        try:
            data = _read_yaml(path)
        except yaml.YAMLError as exc:
            self.issues.append(Issue("error", "L-YAML-001", rel, f"not valid YAML: {exc}"))
            return None
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            for msg in _fmt_errors(exc)[:25]:
                self.issues.append(Issue("error", "L-SCHEMA-001", rel, msg))
            return None

    def parse_dir(self, sub: str, model: type[M]) -> dict[str, M]:
        out: dict[str, M] = {}
        folder = self.root / sub
        if not folder.is_dir():
            self.issues.append(Issue("error", "L-FILE-002", sub, "directory is missing"))
            return out
        for path in sorted(folder.glob("*.yaml")):
            parsed = self.parse(path, model)
            if parsed is not None:
                out[self.rel(path)] = parsed
        return out


def library_checksum(root: Path) -> tuple[str, int]:
    """SHA-256 over every YAML file (sorted path + LF-normalised bytes). Stable across OSes."""
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*.yaml") if p.is_file())
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest(), len(files)


def load_library(root: Path) -> tuple[Library | None, list[Issue]]:
    """Load and schema-validate every file. Returns (library or None, issues)."""
    ld = _Loader(root)
    version = ld.parse(root / "VERSION.yaml", VersionFile)
    taxonomy = ld.parse(root / "taxonomy.yaml", Taxonomy)
    materials = ld.parse(root / "materials.yaml", MaterialsFile)
    units = ld.parse(root / "units.yaml", UnitsFile)
    headers = ld.parse(root / "headers.yaml", HeadersFile)
    confidence = ld.parse(root / "confidence.yaml", ConfidenceFile)
    profiles = ld.parse_dir("profiles", Profile)
    synonyms = ld.parse_dir("synonyms", SynonymFile)
    standards = ld.parse_dir("standards", StandardTable)
    rules = ld.parse_dir("rules", RuleFile)
    archetypes = ld.parse_dir("archetypes", Archetype)

    if (
        version is None
        or taxonomy is None
        or materials is None
        or units is None
        or headers is None
        or confidence is None
    ):
        return None, ld.issues
    checksum, count = library_checksum(root)
    lib = Library(
        root=root,
        version=version,
        taxonomy=taxonomy,
        materials=materials,
        units=units,
        headers=headers,
        confidence=confidence,
        profiles=profiles,
        synonyms=synonyms,
        standards=standards,
        rules=rules,
        archetypes=archetypes,
        checksum=checksum,
        file_count=count,
        issues=ld.issues,
    )
    return lib, ld.issues
