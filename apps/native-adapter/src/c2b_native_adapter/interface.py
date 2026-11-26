"""Native CAD adapter interface and the not-configured stub (ADR-003, ADR-004, ADR-018).

The adapter turns a native CAD file into things the normal pipeline can read:

* Fusion (`.f3d`)  -> a STEP AP242 file.
* SolidWorks (`.sldprt`, `.sldasm`) -> OBJ meshes plus the SVF2 object properties (part number,
  material, custom properties). APS offers no STEP output for SolidWorks.

It holds no database code: the monthly usage cap is reached through the `UsageGate` the caller
passes in, so the worker owns the counter and the adapter stays easy to replace (CAD Exchanger,
a SolidWorks add-in, a customer-run converter).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Status = Literal["ok", "not_configured", "cap_reached", "error"]

SUPPORTED_EXTENSIONS = (".f3d", ".sldprt", ".sldasm")


@dataclass(frozen=True)
class NodeProps:
    """Properties of one object in the converted model, as APS reported them."""

    name: str
    path: str  # names from the root, joined by " / "
    props: dict[str, str]


@dataclass(frozen=True)
class ConversionResult:
    status: Status
    source_format: str = ""
    step_path: Path | None = None
    obj_paths: list[Path] = field(default_factory=list)
    properties: list[NodeProps] = field(default_factory=list)
    message: str = ""
    usage_jobs: int = 0  # translation jobs that were counted against the monthly cap
    notes: list[str] = field(default_factory=list)  # facts for the review notices


@dataclass(frozen=True)
class UsageGate:
    """The monthly cap, owned by the caller. `reserve` is atomic: True means the jobs may run."""

    reserve: Callable[[int], Awaitable[bool]]
    refund: Callable[[int], Awaitable[None]]


class NativeAdapter(Protocol):
    def jobs_needed(self, file_path: Path) -> int:
        """Translation jobs this file will count against the cap."""
        ...

    async def convert(self, file_path: Path, out_dir: Path, gate: UsageGate) -> ConversionResult:
        """Convert `file_path`; write results under `out_dir`. Never raises for service errors."""
        ...


class StubNativeAdapter:
    """Used when no converter is configured. Advises exporting STEP AP242 directly."""

    def jobs_needed(self, file_path: Path) -> int:
        return 0

    async def convert(self, file_path: Path, out_dir: Path, gate: UsageGate) -> ConversionResult:
        suffix = file_path.suffix.lower()
        return ConversionResult(
            status="not_configured",
            source_format=suffix.lstrip("."),
            message=(
                f"Native conversion for '{suffix}' isn't set up on this server. "
                "Please export your model as a STEP AP242 (.step / .stp) file from your CAD "
                "software."
            ),
        )


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ConversionResult",
    "NativeAdapter",
    "NodeProps",
    "Status",
    "StubNativeAdapter",
    "UsageGate",
]
