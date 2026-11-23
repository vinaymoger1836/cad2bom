"""Record and replay cassettes for LLM calls (docs/02 §4, docs/10 §4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Cassette:
    """A recorded LLM interaction."""

    key: str
    role: str
    model: str
    prompt_summary: str
    response: dict[str, Any] | str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


class CassetteStore:
    """Manages recorded cassettes on disk."""

    def __init__(self, base_dir: Path | str = "cassettes") -> None:
        self.base_dir = Path(base_dir)

    @staticmethod
    def make_key(role: str, prompt: str, schema_name: str = "") -> str:
        """Deterministic 16-character hex hash keyed by role, prompt and schema."""
        raw = f"{role}:{prompt.strip()}:{schema_name}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def find(self, role: str, prompt: str, schema_name: str = "") -> Cassette | None:
        """Find a cassette by role and prompt hash across all subdirectories."""
        key = self.make_key(role, prompt, schema_name)
        if not self.base_dir.exists():
            return None

        # Direct file check
        direct = self.base_dir / f"{key}.json"
        if direct.exists():
            return self._load_file(direct)

        # Recursive search in subdirectories (b3, b4, b6, etc.)
        for path in self.base_dir.rglob(f"*{key}*.json"):
            loaded = self._load_file(path)
            if loaded and loaded.key == key:
                return loaded

        # Fallback 1: check if any cassette in base_dir has matching key inside JSON
        for path in self.base_dir.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("key") == key:
                    return Cassette(**data)
            except Exception:  # noqa: S112 - unreadable cassette files are skipped
                continue

        return None

    def save(self, cassette: Cassette, subdir: str | None = None) -> Path:
        """Save a cassette to disk."""
        target_dir = self.base_dir / subdir if subdir else self.base_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{cassette.key}.json"
        file_path.write_text(json.dumps(asdict(cassette), indent=2) + "\n", encoding="utf-8")
        return file_path

    @staticmethod
    def _load_file(path: Path) -> Cassette | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Cassette(**data)
        except Exception:
            return None
