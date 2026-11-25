"""CSV (flat EBOM or MBOM for ERP import) and the JSON exports."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal

from c2b_domain.bom import BomVersion
from c2b_exports.flatten import HEADERS, ExportColumns, ebom_rows, mbom_rows


def build_csv(
    version: BomVersion,
    part: Literal["ebom", "mbom"] = "ebom",
    columns: ExportColumns | None = None,
) -> bytes:
    """One row per line with `level` and `parent`. UTF-8 with a BOM so Excel opens it correctly."""
    cols = columns or ExportColumns()
    if part == "ebom":
        keys, rows = cols.csv, ebom_rows(version)
    else:
        keys = [k for k in ("level", "parent") if k not in cols.mbom] + cols.mbom
        rows = mbom_rows(version)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([HEADERS.get(k, k) for k in keys])
    for r in rows:
        writer.writerow(["" if r.get(k) is None else r.get(k) for k in keys])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def build_json(data: dict[str, Any]) -> bytes:
    """The frozen BomVersion exactly as stored."""
    return (json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")


def build_schema() -> bytes:
    """JSON Schema of the BomVersion JSON (published with the export)."""
    schema = BomVersion.model_json_schema(mode="serialization")
    return (json.dumps(schema, indent=2) + "\n").encode("utf-8")
