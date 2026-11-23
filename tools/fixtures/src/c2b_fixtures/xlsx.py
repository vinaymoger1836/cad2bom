"""B4 (messy supplier XLSX) and B5 (incomplete user BoM) workbooks, plus the facts used for their
expected JSON. Contents are invented; no real company or person is described."""

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

B4_HEADERS = ["Pos", "Teilenummer", "Item", "Menge", "Unit", "Designator", "Hersteller", "MPN", "Bemerkung"]

# (Pos, Teilenummer, Item, Menge, Unit, Designator, Hersteller, MPN, Bemerkung, expected classes)
B4_ROWS: list[tuple[Any, ...]] = [
    (1, " PB-0001 ", "PCBA, BMS + boost converter, 10000mAh power bank", 1, "pcs", None, "Example Electronics", None, "Main board REV C",
     ["electrical.pcba.bms", "electrical.pcba"]),
    (2, "PB-0002", "Li-ion cell 18650 3.7V 3500mAh", 3, "nos", "BT1-BT3", "Samsung SDI", "INR18650-35E", None,
     ["electrical.battery.li_ion"]),
    (3, "PB-0003", "USB-C receptacle 24-pin, right-angle SMD", 1, "pcs", "J1", "Example Connectors", "TYPEC-24-RA", None,
     ["electrical.connector.usb_c"]),
    (4, "PB-0004", "USB-A receptacle, vertical", 1, "pcs", "J2", None, None, None, ["electrical.connector.usb_a"]),
    (5, "PB-0005", "LED 0603 green", 4, "pcs", "D1-D4", "Everlight", "19-217/GHC", "charge level indicator",
     ["electrical.component.led"]),
    (6, "PB-0006", "Resistor 10k 1% 0603", 5, "pcs", "R1-R4, R7", "Yageo", "RC0603FR-0710KL", None,
     ["electrical.component.resistor"]),
    (7, "PB-0007", "Capacitor 100nF 16V X7R 0603", 4, "pcs", "C1-C3, C5", "Murata", "GRM188R71C104KA01D", None,
     ["electrical.component.capacitor"]),
    (8, "PB-0008", "IC, battery charger / boost converter", 1, "nos", "U1", None, "IP5310", None,
     ["electrical.component.ic"]),
    (9, "PB-0009", "Inductor 1uH 4A", 1, "pcs", "L1", None, None, None, ["electrical.component.inductor"]),
    (10, "PB-0010", "Tactile switch 3x4x2", 1, "pcs", "SW1", None, None, None, ["electrical.switch.tactile"]),
    (11, "PB-0011", "Housing, top, ABS, black", 1, "pcs", None, None, None, "tooling in progress",
     ["moulded.housing.top", "moulded.housing"]),
    (12, "PB-0012", "Housing, bottom, ABS, black", 1, "pcs", None, None, None, None,
     ["moulded.housing.bottom", "moulded.housing"]),
    (13, "PB-0013", "Screw PT 2.0x6 self-tapping", 4, "pcs", None, None, None, None,
     ["fastener.screw.self_tapping", "fastener.screw"]),
    (14, "PB-0014", "Rating label 40x20mm", 1, "nos", None, None, None, None, ["label.rating", "label.generic"]),
]

B5_HEADERS = ["Item", "Part No", "Description", "Qty", "Unit", "Material"]
B5_ROWS: list[tuple[Any, ...]] = [
    (1, "BRK-1001", "Bracket, sheet metal, 2mm", 1, "pcs", "CRCA"),
    (2, "CVR-1002", "Cover, moulded", 1, "pcs", "ABS"),
    (3, "BLK-1003", "Aluminium block 40x30x25", 1, "pcs", "AL6061"),
    (4, "SCR-1004", "Screw M4x12 ISO 4762", 6, "pcs", None),
]


def _fix_properties(wb: Workbook) -> None:
    """Fixed metadata so regenerating the workbook gives identical bytes."""
    stamp = datetime(2026, 11, 20)
    wb.properties.creator = "c2b-fixtures"
    wb.properties.created = stamp
    wb.properties.modified = stamp


def _save(wb: Workbook, path: Path) -> None:
    """Save, then rewrite the zip with fixed entry timestamps (byte-identical regeneration)."""
    _fix_properties(wb)
    tmp = path.with_suffix(".tmp")
    wb.save(tmp)
    with zipfile.ZipFile(tmp) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in sorted(zin.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, zin.read(name))
    tmp.unlink()


def write_b4(path: Path) -> list[dict[str, Any]]:
    """Messy supplier file: title block, blank rows, mixed EN/DE headers, grouped refdes,
    units pcs/nos, padded part numbers, a footer. Returns the rows as written (for `raw`)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws["A1"] = "Example Power Systems Ltd."
    ws["A2"] = "BOM - PB-10K power bank - Rev C"
    ws["A3"] = "Prepared by: A. Example    Date: 2026-03-14"
    # row 4-5 blank
    header_row = 6
    for c, h in enumerate(B4_HEADERS, start=1):
        cell = ws.cell(row=header_row, column=c, value=h)
        cell.font = Font(bold=True)
    raw: list[dict[str, Any]] = []
    for r, row in enumerate(B4_ROWS, start=header_row + 1):
        values = row[: len(B4_HEADERS)]
        for c, v in enumerate(values, start=1):
            if v is not None:
                ws.cell(row=r, column=c, value=v)
        raw.append({"row": r, **{h: v for h, v in zip(B4_HEADERS, values, strict=True)}})
    last = header_row + len(B4_ROWS)
    ws.cell(row=last + 2, column=3, value="Total lines")
    ws.cell(row=last + 2, column=4, value=len(B4_ROWS))
    ws.cell(row=last + 3, column=1, value="Prices excluded. Supplier may substitute equivalent parts.")
    for col, w in zip("ABCDEFGHI", (6, 14, 46, 8, 7, 14, 18, 22, 22), strict=True):
        ws.column_dimensions[col].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(wb, path)
    return raw


def write_b5(path: Path) -> list[dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    for c, h in enumerate(B5_HEADERS, start=1):
        ws.cell(row=1, column=c, value=h).font = Font(bold=True)
    raw: list[dict[str, Any]] = []
    for r, row in enumerate(B5_ROWS, start=2):
        for c, v in enumerate(row, start=1):
            if v is not None:
                ws.cell(row=r, column=c, value=v)
        raw.append({"row": r, **{h: v for h, v in zip(B5_HEADERS, row, strict=True)}})
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(wb, path)
    return raw
