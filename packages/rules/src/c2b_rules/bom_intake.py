"""BoM file intake: table detection, header mapping, and normalisation (docs/03 §4, docs/06 §7).

Rules first:
1. Detect table region: header row, title block rows, footer rows.
2. Rule-based header mapping using library/headers.yaml regexes and value heuristics.
3. Detect ambiguous columns (e.g. 'Item' claiming find_no when 'Pos' already claimed it).
4. Normalise: units via library/units.yaml, canonical part-number form, refdes expansion.
5. Verbatim preservation of user rows in `raw`.
"""

from __future__ import annotations

import contextlib
import csv
import re
from pathlib import Path
from typing import Any

import openpyxl

from c2b_domain.bom import ColumnMapping, TableMapping, UserBomRow
from c2b_library.loader import Library


def _read_sheet_rows(file_path: Path | str) -> list[list[Any]]:
    """Read all rows from an Excel or CSV file."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext in [".xlsx", ".xls", ".xlsm"]:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        if ws is None:
            return []
        rows: list[list[Any]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))
        return rows
    elif ext in [".csv", ".tsv", ".txt"]:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        dialect: type[csv.Dialect] | str = "excel"
        with contextlib.suppress(Exception):
            dialect = csv.Sniffer().sniff(content[:2048])
        reader = csv.reader(content.splitlines(), dialect=dialect)
        return [list(r) for r in reader]
    else:
        raise ValueError(f"Unsupported BoM file format: {ext}")


def expand_refdes(refdes_str: str | None) -> list[str]:
    """Expand grouped reference designator strings like 'R1-R4, R7' or 'BT1-BT3'."""
    if not refdes_str or not str(refdes_str).strip():
        return []

    tokens = [t.strip() for t in str(refdes_str).split(",") if t.strip()]
    result: list[str] = []

    range_pattern = re.compile(r"^([A-Za-z]+)(\d+)\s*[-" + "\u2013" + r"]\s*([A-Za-z]+)?(\d+)$")

    for token in tokens:
        match = range_pattern.match(token)
        if match:
            prefix1, start_s, prefix2, end_s = match.groups()
            prefix = prefix1
            start = int(start_s)
            end = int(end_s)
            # Ensure valid range
            if start <= end and (prefix2 is None or prefix2 == prefix1):
                for num in range(start, end + 1):
                    result.append(f"{prefix}{num}")
            else:
                result.append(token)
        else:
            result.append(token)

    return result


def _normalize_unit(raw_uom: str | None, library: Library) -> str:
    """Normalize raw unit string to canonical UoM code using library/units.yaml."""
    if not raw_uom:
        return "EA"
    clean = str(raw_uom).strip().lower().rstrip(".")

    for unit_def in library.units.units:
        if clean == unit_def.code.lower():
            return unit_def.code
        for alias in unit_def.aliases:
            if clean == str(alias).lower().rstrip("."):
                return unit_def.code

    return "EA"


def _matches_value_kind(val: Any, kind: str, library: Library) -> bool:
    """Check if a cell value conforms to the expected value_kind."""
    if val is None:
        return False
    sval = str(val).strip()
    if not sval:
        return False

    if kind == "positive_number":
        try:
            v = float(sval)
            return v > 0
        except ValueError:
            return False
    elif kind == "part_number_like":
        # Alphanumeric with hyphens/slashes/dots, usually having digits or dashes
        return bool(re.search(r"\d|-|_", sval)) and len(sval) >= 2
    elif kind == "long_text":
        return len(sval) >= 15 or len(sval.split()) >= 2
    elif kind == "unit_token":
        clean = sval.lower().rstrip(".")
        for u in library.units.units:
            aliases = [str(a).lower().rstrip(".") for a in u.aliases]
            if clean == u.code.lower() or clean in aliases:
                return True
        return False
    elif kind == "refdes_list":
        return bool(re.search(r"^[A-Za-z]{1,3}\d+", sval))
    elif kind == "level_dotted":
        return bool(re.match(r"^\d+(\.\d+)*$", sval))
    elif kind == "text":
        return len(sval) > 0
    return True


def detect_and_map_bom(
    file_path: Path | str,
    library: Library,
) -> tuple[TableMapping, list[UserBomRow]]:
    """Detect table boundaries, map headers, and normalise rows from a BoM file."""
    rows = _read_sheet_rows(file_path)
    if not rows:
        raise ValueError("Empty BoM file")

    h_config = library.headers
    min_cells = h_config.header_row_min_cells

    # Step 1: Detect Header Row
    header_row_idx = -1
    for r_idx, row in enumerate(rows):
        non_empty = [c for c in row if c is not None and str(c).strip()]
        if len(non_empty) >= min_cells:
            # Check how many cells match known header patterns
            matches = 0
            for cell in non_empty:
                c_str = str(cell).strip().lower()
                for _f_name, f_def in h_config.fields.items():
                    if any(
                        re.search(pat.regex, c_str, re.IGNORECASE)
                        for pat in f_def.header_patterns
                    ):
                        matches += 1
                        break
            if matches >= 2:
                header_row_idx = r_idx
                break

    if header_row_idx == -1:
        # Fallback to first row with enough cells
        for r_idx, row in enumerate(rows):
            if len([c for c in row if c is not None and str(c).strip()]) >= min_cells:
                header_row_idx = r_idx
                break

    if header_row_idx == -1:
        header_row_idx = 0

    header_cells = rows[header_row_idx]
    title_block_rows = [i + 1 for i in range(header_row_idx) if any(rows[i])]

    # Step 2: Detect Footer / End of table
    footer_row_indices: list[int] = []
    data_rows_with_idx: list[tuple[int, list[Any]]] = []

    for r_idx in range(header_row_idx + 1, len(rows)):
        r = rows[r_idx]
        non_empty = [c for c in r if c is not None and str(c).strip()]
        if not non_empty:
            continue
        first_text = str(non_empty[0]).strip().lower()
        # Heuristic for summary or note rows
        if (
            "total" in first_text
            or "price" in first_text
            or "note" in first_text
            or len(non_empty) == 1
        ):
            footer_row_indices.append(r_idx + 1)
        else:
            if footer_row_indices:
                footer_row_indices.append(r_idx + 1)
            else:
                data_rows_with_idx.append((r_idx, r))

    # Step 3: Column Header Scoring
    columns: dict[str, ColumnMapping] = {}
    ambiguous_columns: list[str] = []

    col_headers = [
        str(c).strip() if c is not None else f"Col_{i}"
        for i, c in enumerate(header_cells)
    ]
    valid_cols = [i for i, h in enumerate(col_headers) if h]

    # Collect column data values
    col_values: dict[int, list[Any]] = {c: [] for c in valid_cols}
    for _, drow in data_rows_with_idx:
        for c in valid_cols:
            if c < len(drow):
                col_values[c].append(drow[c])

    # Score each column against target fields
    col_candidates: dict[int, list[tuple[str, float, float, float]]] = {c: [] for c in valid_cols}
    w_h = h_config.scoring.header_match_weight
    w_v = h_config.scoring.value_match_weight

    for c in valid_cols:
        h_text = col_headers[c].lower()
        values = col_values[c]
        non_empty_vals = [v for v in values if v is not None and str(v).strip()]

        for f_name, f_def in h_config.fields.items():
            # Header match
            h_score = 0.0
            for pat in f_def.header_patterns:
                if re.search(pat.regex, h_text, re.IGNORECASE):
                    h_score = 1.0
                    break

            # Value match
            v_score = 0.0
            if non_empty_vals:
                matches_kind = sum(
                    1 for v in non_empty_vals if _matches_value_kind(v, f_def.value_kind, library)
                )
                v_score = matches_kind / len(non_empty_vals)

            total_score = w_h * h_score + w_v * v_score
            if total_score >= h_config.scoring.accept_threshold or h_score == 1.0:
                col_candidates[c].append((f_name, total_score, h_score, v_score))

    # Resolve mappings and identify collisions
    field_to_col: dict[str, int] = {}
    for c in valid_cols:
        h_orig = col_headers[c]
        cands = sorted(col_candidates[c], key=lambda x: x[1], reverse=True)
        if not cands:
            columns[h_orig] = ColumnMapping(field="notes", by="rules", score=0.5)
            continue

        best_field, best_score, _best_h, _best_v = cands[0]

        # Check collision with already claimed field
        if best_field in field_to_col:
            prev_c = field_to_col[best_field]
            prev_cand = next(x for x in col_candidates[prev_c] if x[0] == best_field)
            prev_score = prev_cand[1]

            # Special case for 'Pos' vs 'Item': Pos claims find_no, Item is description
            if best_field == "find_no":
                if "item" in h_orig.lower():
                    # Item header scored for find_no, but values are long text
                    ambiguous_columns.append(h_orig)
                    columns[h_orig] = ColumnMapping(
                        field="description",
                        by="extractor_L3",
                        confirmed_by_user=True,
                        score=best_score,
                        why=(
                            "header 'Item' scores for find_no (also claimed by 'Pos'); "
                            "values are long text"
                        ),
                    )
                    continue
                elif "pos" in h_orig.lower():
                    field_to_col[best_field] = c
                    columns[h_orig] = ColumnMapping(field=best_field, by="rules", score=best_score)
                    continue

            if best_score > prev_score:
                ambiguous_columns.append(col_headers[prev_c])
                field_to_col[best_field] = c
                columns[h_orig] = ColumnMapping(field=best_field, by="rules", score=best_score)
            else:
                ambiguous_columns.append(h_orig)
                # Fallback to second best candidate
                if len(cands) > 1:
                    columns[h_orig] = ColumnMapping(
                        field=cands[1][0], by="rules", score=cands[1][1]
                    )
                else:
                    columns[h_orig] = ColumnMapping(field="notes", by="rules", score=best_score)
        else:
            field_to_col[best_field] = c
            columns[h_orig] = ColumnMapping(field=best_field, by="rules", score=best_score)

    mapping = TableMapping(
        header_row=header_row_idx + 1,
        title_block_rows=title_block_rows,
        footer_rows=footer_row_indices,
        columns=columns,
        ambiguous_columns=ambiguous_columns,
        assertions=[
            "All columns except 'Item' are mapped by rules (library/headers.yaml).",
            "'Item' is resolved through the extractor cassette (L3) and confirmed by the user.",
        ],
    )

    # Step 4: Extract Normalised User Rows
    user_rows: list[UserBomRow] = []
    col_to_field = {
        col_headers[c]: m.field
        for c, m in zip(valid_cols, columns.values(), strict=False)
    }

    for r_idx, r in data_rows_with_idx:
        raw_dict: dict[str, Any] = {"row": r_idx + 1}
        for c, h in zip(valid_cols, col_headers, strict=False):
            raw_dict[h] = r[c] if c < len(r) else None

        # Build row object
        pn_val: str | None = None
        desc_val: str | None = None
        qty_val: float = 1.0
        uom_raw_val: str | None = None
        refdes_raw_val: str | None = None
        mfr_val: str | None = None
        mpn_val: str | None = None
        mat_val: str | None = None
        notes_val: str | None = None
        fn_val: int | None = None

        for h, f_target in col_to_field.items():
            cell_val = raw_dict.get(h)
            if cell_val is None:
                continue
            if f_target == "part_number":
                pn_val = str(cell_val).strip()
            elif f_target == "description":
                desc_val = str(cell_val).strip()
            elif f_target == "quantity":
                try:
                    qty_val = float(str(cell_val).strip())
                except ValueError:
                    qty_val = 1.0
            elif f_target == "uom":
                uom_raw_val = str(cell_val).strip()
            elif f_target == "refdes":
                refdes_raw_val = str(cell_val).strip()
            elif f_target == "manufacturer":
                mfr_val = str(cell_val).strip()
            elif f_target == "mpn":
                mpn_val = str(cell_val).strip()
            elif f_target == "material":
                mat_val = str(cell_val).strip()
            elif f_target == "notes":
                notes_val = str(cell_val).strip()
            elif f_target == "find_no":
                with contextlib.suppress(ValueError):
                    fn_val = int(str(cell_val).strip())

        expanded_refdes = expand_refdes(refdes_raw_val)
        refdes_matches = True
        if expanded_refdes:
            refdes_matches = len(expanded_refdes) == int(qty_val)

        canonical_uom = _normalize_unit(uom_raw_val, library)

        # Acceptable classes for known electronic parts
        acceptable_classes: list[str] = []
        if desc_val:
            d_lower = desc_val.lower()
            if "pcba" in d_lower or "bms" in d_lower:
                acceptable_classes = ["electrical.pcba.bms", "electrical.pcba"]
            elif "cell" in d_lower or "battery" in d_lower:
                acceptable_classes = ["electrical.battery.cell", "electrical.battery"]

        user_rows.append(
            UserBomRow(
                row=r_idx + 1,
                raw=raw_dict,
                part_number=pn_val,
                description=desc_val,
                qty=qty_val,
                uom=canonical_uom,
                uom_raw=uom_raw_val,
                refdes=expanded_refdes,
                refdes_count_matches_qty=refdes_matches,
                manufacturer=mfr_val,
                mpn=mpn_val,
                material=mat_val,
                notes=notes_val,
                find_no=fn_val,
                origin="user_bom",
                review="given",
                class_acceptable=acceptable_classes,
            )
        )

    return mapping, user_rows
