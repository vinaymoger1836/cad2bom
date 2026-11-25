"""One flat, provenance-free view of a frozen BomVersion that every export format reads.

Unwrapping `Prov | plain` happens here and nowhere else, so the XLSX, CSV and PDF can never
disagree about a value. Nothing is computed that is not already in the version, except roll-ups
(purchased items) that are plain sums of version quantities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from c2b_domain.bom import BomLine, BomVersion, MbomLine
from c2b_domain.prov import Prov

LABEL = "Generated BoM — review before release"

# Header text per column key; the key list itself comes from the organisation profile.
HEADERS: dict[str, str] = {
    "level": "Level",
    "parent": "Parent",
    "find_no": "Find no",
    "part_number": "Part number",
    "revision": "Rev",
    "description": "Description",
    "qty": "Qty",
    "qty_total": "Total qty",
    "uom": "UoM",
    "material": "Material",
    "finish": "Finish",
    "mass_g": "Mass (g)",
    "make_buy": "Make / buy",
    "class": "Class",
    "standard": "Standard",
    "mpn": "MPN",
    "manufacturer": "Manufacturer",
    "refdes": "Ref des",
    "source": "Source",
    "confidence": "Confidence",
    "notes": "Notes",
    "ebom_refs": "EBOM lines",
    "id": "Line",
}

DEFAULT_EBOM_COLUMNS = [
    "find_no",
    "part_number",
    "revision",
    "description",
    "qty",
    "uom",
    "material",
    "finish",
    "mass_g",
    "make_buy",
    "class",
    "standard",
    "mpn",
    "refdes",
    "source",
    "confidence",
    "notes",
]
DEFAULT_MBOM_COLUMNS = [
    "find_no",
    "part_number",
    "description",
    "qty",
    "uom",
    "make_buy",
    "ebom_refs",
    "notes",
]
DEFAULT_CSV_COLUMNS = [
    "level",
    "parent",
    "find_no",
    "part_number",
    "revision",
    "description",
    "qty",
    "uom",
    "material",
    "finish",
    "mass_g",
    "make_buy",
    "notes",
]


@dataclass(frozen=True)
class ExportColumns:
    """Column order per sheet. Comes from the organisation profile (docs/05 §3)."""

    ebom: list[str] = field(default_factory=lambda: list(DEFAULT_EBOM_COLUMNS))
    mbom: list[str] = field(default_factory=lambda: list(DEFAULT_MBOM_COLUMNS))
    csv: list[str] = field(default_factory=lambda: list(DEFAULT_CSV_COLUMNS))


def unwrap(value: Any) -> Any:
    """The plain value of a `Prov`, a `{"value": ...}` dict or a plain value."""
    if isinstance(value, Prov):
        return value.value
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def prov_source(value: Any) -> str | None:
    if isinstance(value, Prov):
        return str(value.source)
    if isinstance(value, dict) and "source" in value:
        return str(value["source"])
    return None


def prov_rules(value: Any) -> list[str]:
    if isinstance(value, Prov):
        return list(value.rules)
    if isinstance(value, dict):
        return [str(r) for r in value.get("rules", [])]
    return []


def num(value: Any) -> int | float | None:
    """Whole numbers stay whole (3, not 3.0); other numbers are rounded to 4 places."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 4)


def _depth_order(ids: list[str], parents: dict[str, str | None]) -> list[tuple[str, int]]:
    """Depth-first order that keeps the list order among siblings. Orphans become roots."""
    known = set(ids)
    children: dict[str | None, list[str]] = {}
    for i in ids:
        p = parents.get(i)
        children.setdefault(p if p in known else None, []).append(i)
    out: list[tuple[str, int]] = []
    seen: set[str] = set()

    def walk(node: str, depth: int) -> None:
        if node in seen:
            return
        seen.add(node)
        out.append((node, depth))
        for c in children.get(node, []):
            walk(c, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)
    for i in ids:  # a cycle would leave lines unvisited; list them rather than lose them
        walk(i, 0)
    return out


def _standard(line: BomLine) -> str:
    d = line.designation
    if d is None:
        return ""
    return d.key or " ".join(p for p in (d.standard, d.size) if p)


def _line_source(line: BomLine) -> str:
    cls_source = prov_source(line.cls)
    return cls_source or str(line.source)


def ebom_rows(version: BomVersion) -> list[dict[str, Any]]:
    """EBOM lines in tree order, each with its depth (0 for top level)."""
    by_id = {ln.id: ln for ln in version.ebom}
    order = _depth_order([ln.id for ln in version.ebom], {ln.id: ln.parent for ln in version.ebom})
    rows: list[dict[str, Any]] = []
    for line_id, depth in order:
        ln = by_id[line_id]
        rows.append(
            {
                "id": ln.id,
                "depth": depth,
                "level": depth + 1,
                "parent": ln.parent or "",
                "find_no": ln.find_no,
                "part_number": unwrap(ln.part_number) or "",
                "revision": ln.revision,
                "description": unwrap(ln.description) or "",
                "qty": num(unwrap(ln.qty_per_parent)),
                "qty_total": num(ln.qty_total),
                "uom": ln.uom,
                "material": unwrap(ln.material) or "",
                "finish": unwrap(ln.finish) or "",
                "mass_g": num(unwrap(ln.mass_g)),
                "make_buy": unwrap(ln.make_buy) or "",
                "class": unwrap(ln.cls) or "",
                "standard": _standard(ln),
                "mpn": ln.mpn or "",
                "manufacturer": ln.manufacturer or "",
                "refdes": ", ".join(ln.refdes),
                "source": _line_source(ln),
                "confidence": round(float(ln.confidence), 2),
                "notes": ln.note or "",
            }
        )
    return rows


def mbom_rows(version: BomVersion) -> list[dict[str, Any]]:
    by_id = {ln.id: ln for ln in version.mbom}
    order = _depth_order([ln.id for ln in version.mbom], {ln.id: ln.parent for ln in version.mbom})
    rows: list[dict[str, Any]] = []
    for line_id, depth in order:
        ln: MbomLine = by_id[line_id]
        rows.append(
            {
                "id": ln.id,
                "depth": depth,
                "level": depth + 1,
                "parent": ln.parent or "",
                "find_no": ln.find_no,
                "part_number": ln.part_number or "",
                "description": ln.description,
                "qty": num(ln.qty_per_parent),
                "qty_total": num(ln.qty_total),
                "uom": ln.uom,
                "make_buy": ln.make_buy,
                "class": ln.cls,
                "ebom_refs": ", ".join(ln.ebom_refs),
                "notes": ln.notes or "",
            }
        )
    return rows


def purchased_rows(version: BomVersion) -> list[dict[str, Any]]:
    """Buy lines of the build view rolled up by part number (or description when there is none).

    Totals are plain sums of the MBOM quantities; the yearly figure needs the annual volume.
    """
    volume = version.product.annual_volume if version.product else None
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for ln in version.mbom:
        if ln.make_buy != "buy":
            continue
        key = (ln.part_number or ln.description, ln.uom)
        g = groups.setdefault(
            key,
            {
                "part_number": ln.part_number or "",
                "description": ln.description,
                "class": ln.cls,
                "uom": ln.uom,
                "qty_per_build": 0.0,
                "lines": [],
            },
        )
        g["qty_per_build"] += float(ln.qty_total)
        g["lines"].append(ln.id)
    rows: list[dict[str, Any]] = []
    for g in groups.values():
        per_build = float(g["qty_per_build"])
        rows.append(
            {
                "part_number": g["part_number"],
                "description": g["description"],
                "class": g["class"],
                "uom": g["uom"],
                "qty_per_build": num(per_build),
                "qty_per_year": num(per_build * volume) if volume else None,
                "mbom_lines": ", ".join(g["lines"]),
            }
        )
    return sorted(rows, key=lambda r: (str(r["part_number"]) or "~", str(r["description"])))


def open_item_rows(version: BomVersion) -> list[dict[str, Any]]:
    """Open items and validation warnings, one row each."""
    rows: list[dict[str, Any]] = []
    for item in version.open_items:
        ref, sep, text = item.partition(": ")
        rows.append({"kind": "Open item", "ref": ref if sep else "", "text": text if sep else item})
    if version.validation:
        for w in version.validation.warnings:
            rows.append(
                {
                    "kind": "Warning",
                    "ref": w.line_id or "",
                    "text": f"{w.message} ({w.rule_id})" if w.rule_id else w.message,
                }
            )
    for q in version.questions:
        if q.answer is None:
            rows.append({"kind": "Open question", "ref": ", ".join(q.line_ids), "text": q.text})
    for s in version.suggestions:
        if s.review == "suggested":
            rows.append({"kind": "Open suggestion", "ref": s.id, "text": s.text or str(s.cls)})
    return rows


def excluded_rows(version: BomVersion) -> list[dict[str, Any]]:
    return [
        {
            "id": n.id,
            "name": n.name,
            "kind": n.kind,
            "reason": n.excluded or "",
            "instances": n.instance_count,
        }
        for n in version.excluded
    ]


def explanation_rows(version: BomVersion) -> list[dict[str, Any]]:
    """Line -> rule ids and evidence, so any value can be traced (CLAUDE.md rule 7)."""
    rows: list[dict[str, Any]] = []
    for ln in version.ebom:
        rules: list[str] = list(ln.rule_ids)
        provs: list[Any] = [
            ln.cls,
            ln.part_number,
            ln.description,
            ln.qty_per_parent,
            ln.material,
            ln.finish,
            ln.mass_g,
            ln.make_buy,
        ]
        for prov in provs:
            for r in prov_rules(prov):
                if r not in rules:
                    rules.append(r)
        rows.append(
            {
                "id": ln.id,
                "part_number": unwrap(ln.part_number) or "",
                "description": unwrap(ln.description) or "",
                "class": unwrap(ln.cls) or "",
                "source": _line_source(ln),
                "confidence": round(float(ln.confidence), 2),
                "rules": ", ".join(rules),
                "evidence": " | ".join(ln.explanation),
                "cad_nodes": ", ".join(ln.cad_nodes),
            }
        )
    return rows


def about_rows(version: BomVersion) -> list[tuple[str, Any]]:
    product = version.product
    return [
        ("Product", product.name if product else ""),
        ("BoM version", version.version),
        ("Created", version.created_at or ""),
        ("Job", version.job_id),
        ("Library version", version.library_version),
        ("Annual volume", product.annual_volume if product and product.annual_volume else ""),
        ("EBOM lines", len(version.ebom)),
        ("MBOM lines", len(version.mbom)),
        ("Open items", len(version.open_items)),
        ("LLM calls", version.llm_calls),
        ("Status", LABEL),
    ]
