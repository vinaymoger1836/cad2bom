"""Part numbering engine per organisation profile (docs/04 §10, docs/06 §6).

Rules:
1. Existing user and CAD part numbers are strictly preserved.
2. Standard catalog parts with designations receive deterministic designation keys.
3. Unnumbered lines receive sequential numbers based on profile prefix and sequence.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from c2b_domain.bom import BomLine
from c2b_domain.prov import Prov
from c2b_library.loader import Library

log = logging.getLogger(__name__)

DEFAULT_PREFIXES = {
    "fastener": "FAS",
    "bearing": "BRG",
    "transmission": "TRN",
    "seal": "SEL",
    "spring": "SPR",
    "sheetmetal": "SHM",
    "machined": "MCH",
    "moulded": "MLD",
    "cast": "CST",
    "extrusion": "EXT",
    "wood": "WDP",
    "glass": "GLS",
    "electrical": "ELE",
    "fluid": "FLD",
    "packaging": "PKG",
    "label": "LBL",
    "documentation": "DOC",
    "consumable": "CON",
    "purchased": "PUR",
    "assembly": "ASM",
    "unclassified": "UNC",
    "default": "GEN",
}


def get_class_prefix(cls_code: str, prefixes: dict[str, str] | None = None) -> str:
    """Resolve 3-letter uppercase prefix for a taxonomy class code."""
    prefs = prefixes or DEFAULT_PREFIXES
    top_level = cls_code.split(".")[0]
    return prefs.get(top_level, prefs.get("default", "GEN"))


def assign_part_numbers(
    lines: list[BomLine],
    library: Library | None = None,
    profile_name: str = "default",
    start_seq: int = 10,
    step_seq: int = 10,
) -> list[BomLine]:
    """Assign part numbers to unnumbered lines using the profile scheme."""
    prefixes = DEFAULT_PREFIXES
    if library and library.profile and library.profile.numbering:
        p_prefs = getattr(library.profile.numbering, "prefixes", None)
        if isinstance(p_prefs, dict):
            prefixes = p_prefs

    category_counters: dict[str, int] = {}
    current_seq = start_seq

    for line in lines:
        # 1. If part number already assigned, keep it
        pn_val = line.part_number
        if pn_val:
            if isinstance(pn_val, Prov) and pn_val.value and str(pn_val.value).strip():
                continue
            if isinstance(pn_val, str) and pn_val.strip():
                continue

        cls_str = line.cls if isinstance(line.cls, str) else line.cls.value
        prefix = get_class_prefix(cls_str, prefixes)

        # 2. Standard part with designation -> deterministic designation key
        if line.designation is not None:
            desig = line.designation
            std_clean = re.sub(r"[^A-Za-z0-9]", "", desig.standard).upper()
            size_clean = desig.size.upper()
            if desig.length_mm:
                size_clean += f"X{int(desig.length_mm)}"
            key_pn = f"{prefix}-{std_clean}-{size_clean}"

            line.part_number = Prov(
                value=key_pn,
                source="std_table",
                confidence=0.95,
                rules=["R-NUM-STD"],
                note=f"Standard part designation key for {desig.standard} {desig.size}",
            )
            continue

        # 3. Custom component -> sequential number per profile
        seq_num = category_counters.get(prefix, current_seq)
        category_counters[prefix] = seq_num + step_seq
        current_seq += step_seq

        assigned_pn = f"{prefix}-{seq_num:05d}"
        line.part_number = Prov(
            value=assigned_pn,
            source="library",
            confidence=0.80,
            rules=["R-NUM-SEQ"],
            note=f"Assigned sequential part number using profile prefix '{prefix}'",
        )

    return lines
