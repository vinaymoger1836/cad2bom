"""Photo step and minimal vision router stub (docs/03 §5).

Calls the 'vision' role through a minimal router stub that returns cassette data
in tests; findings stored with low confidence (<= 0.40).
Never produces dimensions or masses.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from c2b_domain.bom import PhotoFinding


class VisionRouterStub:
    """Minimal vision router stub with cassette record/replay support."""

    def __init__(self, cassette_dir: Path | str | None = None) -> None:
        self.cassette_dir = Path(cassette_dir) if cassette_dir else None

    def analyze_photo(
        self,
        photo_path: Path | str,
        job_id: str = "default",
        opt_in: bool = False,
    ) -> list[PhotoFinding]:
        """Analyze a photo and return visual findings."""
        if not opt_in:
            return []

        path = Path(photo_path)
        if not path.exists():
            return []

        # Check for cassette if directory is configured
        if self.cassette_dir and self.cassette_dir.exists():
            cassette_file = self.cassette_dir / f"vision_{path.stem}.json"
            if cassette_file.exists():
                with contextlib.suppress(Exception):
                    data = json.loads(cassette_file.read_text(encoding="utf-8"))
                    findings: list[PhotoFinding] = []
                    for item in data.get("findings", []):
                        findings.append(
                            PhotoFinding(
                                item_description=item.get("description", "Visible component"),
                                candidate_class=item.get("class"),
                                count=item.get("count", 1),
                                detected_text=item.get("detected_text", []),
                                confidence=min(float(item.get("confidence", 0.35)), 0.40),
                                ref=f"photo:{path.name}",
                            )
                        )
                    return findings

        # Default stub output for placeholder/opt-in photos
        return [
            PhotoFinding(
                item_description=f"Visual object detected in {path.name}",
                candidate_class=None,
                count=1,
                detected_text=[],
                confidence=0.30,
                ref=f"photo:{path.name}",
            )
        ]


def process_photos(
    photos: list[Path | str],
    opt_in: bool = False,
    cassette_dir: Path | str | None = None,
) -> list[PhotoFinding]:
    """Process a list of product photos using the vision router stub."""
    router = VisionRouterStub(cassette_dir=cassette_dir)
    findings: list[PhotoFinding] = []
    for p in photos:
        findings.extend(router.analyze_photo(p, opt_in=opt_in))
    return findings
