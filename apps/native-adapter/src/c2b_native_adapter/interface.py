"""Native CAD adapter interface and stub implementation (ADR-003, ADR-004)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class ConvertedModel:
    """Result of converting a native CAD file to STEP AP242."""

    status: Literal["ok", "not_configured", "error"]
    step_path: Path | None = None
    message: str = ""
    source_format: str = ""


class NativeAdapter(Protocol):
    """Interface for native CAD translation services (e.g. Autodesk Platform Services)."""

    def convert(self, file_path: Path) -> ConvertedModel:
        """Convert native CAD file to STEP AP242."""
        ...


class StubNativeAdapter:
    """Stub implementation when no cloud or local native CAD converter is configured.

    Advises the user to export STEP AP242 directly from their authoring tool.
    """

    def convert(self, file_path: Path) -> ConvertedModel:
        suffix = file_path.suffix.lower()
        return ConvertedModel(
            status="not_configured",
            step_path=None,
            source_format=suffix,
            message=(
                f"Native CAD translation for '{suffix}' is not configured on this server. "
                "Please export your assembly as a STEP AP242 (.step / .stp) file "
                "from your CAD software."
            ),
        )


__all__ = [
    "ConvertedModel",
    "NativeAdapter",
    "StubNativeAdapter",
]
