"""XLSX workbook (docs/05 §3): EBOM, MBOM, Purchased items, Open items, Excluded, Explanations, About."""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from c2b_domain.bom import BomVersion
from c2b_exports.flatten import (
    HEADERS,
    LABEL,
    ExportColumns,
    about_rows,
    ebom_rows,
    excluded_rows,
    explanation_rows,
    mbom_rows,
    open_item_rows,
    purchased_rows,
)

SHEETS = [
    "EBOM",
    "MBOM",
    "Purchased items",
    "Open items",
    "Excluded from BoM",
    "Explanations",
    "About",
]

_HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_THIN = Side(style="thin", color="D0D5DD")
_BORDER = Border(bottom=_THIN)
_WIDTHS = {
    "description": 46,
    "part_number": 20,
    "material": 24,
    "notes": 36,
    "refdes": 22,
    "standard": 22,
    "class": 28,
    "ebom_refs": 18,
    "evidence": 70,
    "rules": 36,
    "text": 70,
}


def _write(
    ws: Worksheet,
    keys: Sequence[str],
    rows: list[dict[str, Any]],
    headers: dict[str, str] | None = None,
    indent_key: str | None = None,
) -> None:
    names = {**HEADERS, **(headers or {})}
    ws.append([names.get(k, k) for k in keys])
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    for r in rows:
        ws.append([r.get(k) for k in keys])
        row_idx = ws.max_row
        for col, key in enumerate(keys, start=1):
            cell = ws.cell(row=row_idx, column=col)
            cell.border = _BORDER
            if key == indent_key:
                cell.alignment = Alignment(indent=int(r.get("depth", 0)), vertical="top")
            elif key in {"description", "text", "evidence", "notes"}:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(vertical="top")
            if key == "confidence" and cell.value is not None:
                cell.number_format = "0%"
            if key in {"mass_g"} and cell.value is not None:
                cell.number_format = "0.0##"
    for col, key in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(col)].width = _WIDTHS.get(key, 13)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}{ws.max_row}"


def build_xlsx(version: BomVersion, columns: ExportColumns | None = None) -> bytes:
    cols = columns or ExportColumns()
    wb = Workbook()

    ws = wb.active
    assert ws is not None
    ws.title = "EBOM"
    _write(ws, cols.ebom, ebom_rows(version), indent_key="description")

    _write(wb.create_sheet("MBOM"), cols.mbom, mbom_rows(version), indent_key="description")

    purchased = purchased_rows(version)
    _write(
        wb.create_sheet("Purchased items"),
        [
            "part_number",
            "description",
            "class",
            "uom",
            "qty_per_build",
            "qty_per_year",
            "mbom_lines",
        ],
        purchased,
        headers={
            "qty_per_build": "Qty per build",
            "qty_per_year": "Qty per year",
            "mbom_lines": "MBOM lines",
        },
    )

    _write(
        wb.create_sheet("Open items"),
        ["kind", "ref", "text"],
        open_item_rows(version),
        headers={"kind": "Type", "ref": "Line / ref", "text": "Item"},
    )

    _write(
        wb.create_sheet("Excluded from BoM"),
        ["id", "name", "kind", "reason", "instances"],
        excluded_rows(version),
        headers={
            "id": "CAD node",
            "name": "Name",
            "kind": "Kind",
            "reason": "Why excluded",
            "instances": "Instances",
        },
    )

    _write(
        wb.create_sheet("Explanations"),
        [
            "id",
            "part_number",
            "description",
            "class",
            "source",
            "confidence",
            "rules",
            "evidence",
            "cad_nodes",
        ],
        explanation_rows(version),
        headers={"rules": "Rule ids", "evidence": "Evidence", "cad_nodes": "CAD nodes"},
    )

    about = wb.create_sheet("About")
    about.append(["Item", "Value"])
    for cell in about[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    for k, v in about_rows(version):
        about.append([k, v])
    about.column_dimensions["A"].width = 22
    about.column_dimensions["B"].width = 60
    about.append([])
    about.append([LABEL])
    about.cell(row=about.max_row, column=1).font = Font(bold=True, color="B42318")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
