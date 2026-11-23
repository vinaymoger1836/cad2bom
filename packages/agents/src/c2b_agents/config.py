"""Configuration and role definitions for LLM router and agents (docs/02 §4, docs/04 §9)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Role = Literal["reasoner", "extractor", "vision", "agent"]


@dataclass(frozen=True)
class AgentConfig:
    """Agent and router settings."""

    # Models per role
    model_reasoner: str = field(
        default_factory=lambda: os.environ.get(
            "C2B_MODEL_REASONER", "groq/llama-3.3-70b-versatile"
        )
    )
    fallback_reasoner: str = field(
        default_factory=lambda: os.environ.get(
            "C2B_FALLBACK_REASONER", "gemini/gemini-1.5-flash"
        )
    )
    model_extractor: str = field(
        default_factory=lambda: os.environ.get(
            "C2B_MODEL_EXTRACTOR", "groq/llama-3.1-8b-instant"
        )
    )
    fallback_extractor: str = field(
        default_factory=lambda: os.environ.get("C2B_FALLBACK_EXTRACTOR", "reasoner")
    )
    model_vision: str = field(
        default_factory=lambda: os.environ.get(
            "C2B_MODEL_VISION", "gemini/gemini-1.5-flash"
        )
    )
    fallback_vision: str | None = None  # None: photo step skipped with a notice per doc 02 §4

    model_agent: str = field(
        default_factory=lambda: os.environ.get(
            "C2B_MODEL_AGENT", "groq/llama-3.3-70b-versatile"
        )
    )
    fallback_agent: str = field(
        default_factory=lambda: os.environ.get("C2B_FALLBACK_AGENT", "reasoner")
    )

    # Budgets & Limits
    job_token_budget: int = field(
        default_factory=lambda: int(os.environ.get("C2B_JOB_TOKEN_BUDGET", "40000"))
    )

    # Per-role RPM & TPM token buckets
    max_rpm: dict[str, int] = field(
        default_factory=lambda: {
            "reasoner": 30,
            "extractor": 30,
            "vision": 15,
            "agent": 30,
        }
    )
    max_tpm: dict[str, int] = field(
        default_factory=lambda: {
            "reasoner": 6000,
            "extractor": 14000,
            "vision": 32000,
            "agent": 6000,
        }
    )

    # 429 retry backoff
    retry_max: int = 3
    retry_backoff_s: float = 1.0

    # Cassettes
    cassette_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("C2B_CASSETTE_DIR", "cassettes"))
    )
    cassette_mode: str = field(
        default_factory=lambda: os.environ.get("C2B_CASSETTE_MODE", "auto")
    )  # "auto" | "replay" | "record" | "off"


_CONFIG: AgentConfig | None = None


def get_agent_config() -> AgentConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = AgentConfig()
    return _CONFIG
