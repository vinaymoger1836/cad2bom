"""Datasheet PDF extraction: text, tables, and regex extractors per spec family (docs/03 §5).

Extracts dimensions, mass, materials, voltage, current, power, and IP ratings
with exact page references (e.g. 'pdf:spec.pdf#p2').
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from c2b_domain.bom import DatasheetSpec

# Regex patterns for spec extraction
_DIM_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:x|"
    + "\u00d7"
    + r"|by)\s*(\d+(?:\.\d+)?)(?:\s*(?:x|"
    + "\u00d7"
    + r"|by)\s*(\d+(?:\.\d+)?))?\s*(mm|cm|m|in)\b",
    re.IGNORECASE,
)
_MASS_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(g|kg|mg|lbs|oz)\b", re.IGNORECASE)
_VOLTAGE_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(V(?:AC|DC)?|kV|mV)\b", re.IGNORECASE)
_CURRENT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(A|mA|µA|uA)\b")
_POWER_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(W|kW|mW)\b")
_IP_PATTERN = re.compile(r"\b(IP\s*[0-9]{2}(?:[A-Za-z])?)\b", re.IGNORECASE)
_STANDARD_PATTERN = re.compile(
    r"\b((?:ISO|DIN|IEC|EN|IS|ASTM)\s*[0-9]+(?:-[0-9]+)?)\b",
    re.IGNORECASE,
)
_MATERIAL_PATTERN = re.compile(
    r"(?:Material|Werkstoff|Matière)\s*[:=]\s*([A-Za-z0-9_\-\s]{2,30})",
    re.IGNORECASE,
)


def extract_datasheet_specs(pdf_path: Path | str) -> list[DatasheetSpec]:
    """Extract structured engineering specifications from a datasheet PDF."""
    path = Path(pdf_path)
    if not path.exists():
        return []

    specs: list[DatasheetSpec] = []
    filename = path.name

    with pdfplumber.open(path) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            ref_str = f"pdf:{filename}#p{page_idx}"

            # 1. Dimensions
            for m in _DIM_PATTERN.finditer(page_text):
                dims = [float(d) for d in m.groups()[:-1] if d is not None]
                unit = m.groups()[-1]
                specs.append(
                    DatasheetSpec(
                        spec_type="dimension",
                        value=dims,
                        unit=unit,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 2. Mass
            for m in _MASS_PATTERN.finditer(page_text):
                val_s, unit_s = m.groups()
                specs.append(
                    DatasheetSpec(
                        spec_type="mass",
                        value=float(val_s),
                        unit=unit_s,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 3. Voltage
            for m in _VOLTAGE_PATTERN.finditer(page_text):
                val_s, unit_s = m.groups()
                specs.append(
                    DatasheetSpec(
                        spec_type="voltage",
                        value=float(val_s),
                        unit=unit_s,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 4. Current
            for m in _CURRENT_PATTERN.finditer(page_text):
                val_s, unit_s = m.groups()
                specs.append(
                    DatasheetSpec(
                        spec_type="current",
                        value=float(val_s),
                        unit=unit_s,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 5. Power
            for m in _POWER_PATTERN.finditer(page_text):
                val_s, unit_s = m.groups()
                specs.append(
                    DatasheetSpec(
                        spec_type="power",
                        value=float(val_s),
                        unit=unit_s,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 6. IP rating
            for m in _IP_PATTERN.finditer(page_text):
                raw_ip = m.group(1).replace(" ", "").upper()
                specs.append(
                    DatasheetSpec(
                        spec_type="ip_rating",
                        value=raw_ip,
                        unit=None,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 7. Standards
            for m in _STANDARD_PATTERN.finditer(page_text):
                std_s = re.sub(r"\s+", " ", m.group(1)).upper()
                specs.append(
                    DatasheetSpec(
                        spec_type="standard",
                        value=std_s,
                        unit=None,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

            # 8. Material
            for m in _MATERIAL_PATTERN.finditer(page_text):
                mat_s = m.group(1).strip()
                specs.append(
                    DatasheetSpec(
                        spec_type="material",
                        value=mat_s,
                        unit=None,
                        raw_text=m.group(0),
                        page=page_idx,
                        ref=ref_str,
                        confidence=0.85,
                    )
                )

    return specs
