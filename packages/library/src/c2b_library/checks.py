"""Library consistency checks (docs/06 §8). Schema validation happens in the loader; these are
the cross-file checks: references, standard-table sanity, synonym collisions, rule ids."""

import re
import unicodedata
from collections import defaultdict
from string import Formatter

from c2b_library.loader import Issue, Library
from c2b_library.schemas import StandardTable

_RULE_PREFIX_RE = re.compile(r"^(R-[A-Z]{3})-\d{3}$")
_SIZE_NUM_RE = re.compile(r"^M(\d+(?:\.\d+)?)$")


def norm_token(token: str) -> str:
    t = unicodedata.normalize("NFKC", token).casefold().strip()
    return re.sub(r"\s+", " ", t)


def norm_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def run_checks(lib: Library) -> list[Issue]:
    issues: list[Issue] = []
    issues += _check_taxonomy(lib)
    issues += _check_materials_profiles(lib)
    issues += _check_units(lib)
    issues += _check_standards(lib)
    issues += _check_synonyms(lib)
    issues += _check_rules(lib)
    issues += _check_archetypes(lib)
    issues += _check_headers(lib)
    issues += _check_verify_summary(lib)
    return issues


# --- helpers -----------------------------------------------------------------------------------


def class_codes(lib: Library) -> set[str]:
    return {c.code for c in lib.taxonomy.classes}


def class_refs(lib: Library) -> set[str]:
    """Valid targets of a class reference: explicit classes plus branch prefixes (e.g. `fastener`)."""
    codes = class_codes(lib)
    refs = set(codes)
    for code in codes:
        parts = code.split(".")
        refs.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return refs


def _walk(obj: object, wanted: set[str]) -> list[str]:
    """Collect string values under any key in `wanted`, anywhere inside a rule body."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in wanted:
                if isinstance(v, str):
                    found.append(v)
                elif isinstance(v, list):
                    found.extend(str(x) for x in v if isinstance(x, str))
            found.extend(_walk(v, wanted))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk(item, wanted))
    return found


# --- taxonomy ----------------------------------------------------------------------------------


def _check_taxonomy(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    f = "taxonomy.yaml"
    seen: set[str] = set()
    units = {u.code for u in lib.units.units}
    fields = set(lib.taxonomy.template_fields)
    profile_keys = {k for p in lib.profiles.values() for k in p.defaults}
    std_keys = _standard_keys(lib)
    explicit = {c.code for c in lib.taxonomy.classes}
    no_table: set[str] = set()
    no_parent: set[str] = set()

    for tmpl_owner, tmpl in [("default_template", lib.taxonomy.default_template)] + [
        (c.code, c.description_template) for c in lib.taxonomy.classes if c.description_template
    ]:
        used = {name for _, name, _, _ in Formatter().parse(tmpl) if name}
        bad = used - fields
        if bad:
            out.append(
                Issue(
                    "error",
                    "L-TAX-001",
                    f,
                    f"{tmpl_owner}: template uses unknown field(s) {sorted(bad)}",
                )
            )

    d = lib.taxonomy.defaults
    if d.uom not in units:
        out.append(Issue("error", "L-TAX-002", f, f"default uom {d.uom!r} not in units.yaml"))

    for c in lib.taxonomy.resolved():
        if c.code in seen:
            out.append(Issue("error", "L-TAX-003", f, f"duplicate class code {c.code}"))
        seen.add(c.code)
        if c.uom not in units:
            out.append(Issue("error", "L-TAX-002", f, f"{c.code}: uom {c.uom!r} not in units.yaml"))
        if c.material_default_profile_key and c.material_default_profile_key not in profile_keys:
            out.append(
                Issue(
                    "error",
                    "L-TAX-004",
                    f,
                    f"{c.code}: material_default_profile_key "
                    f"{c.material_default_profile_key!r} is not in any profile's defaults",
                )
            )
        for fam in c.standard_families:
            if norm_key(fam) not in std_keys:
                no_table.add(fam)
        if c.parent and c.parent not in explicit:
            no_parent.add(c.parent)
    if no_table:
        out.append(
            Issue(
                "info",
                "L-TAX-005",
                f,
                f"{len(no_table)} standard families named by classes have no table in this "
                f"version: {', '.join(sorted(no_table))}",
            )
        )
    if no_parent:
        out.append(
            Issue(
                "info",
                "L-TAX-006",
                f,
                f"{len(no_parent)} group codes are implied by their children but not explicit "
                f"classes: {', '.join(sorted(no_parent))}",
            )
        )
    return out


def _standard_keys(lib: Library) -> set[str]:
    keys: set[str] = set()
    for t in lib.standards.values():
        keys.add(norm_key(t.key))
        keys.update(norm_key(a) for a in t.aliases)
        keys.add(norm_key(t.standard))
    return keys


# --- materials, profile ------------------------------------------------------------------------


def _check_materials_profiles(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    f = "materials.yaml"
    codes: set[str] = set()
    fams = set(lib.materials.families)
    systems = set(lib.materials.designation_systems)
    for m in lib.materials.materials:
        if m.code in codes:
            out.append(Issue("error", "L-MAT-001", f, f"duplicate material code {m.code}"))
        codes.add(m.code)
        if m.family not in fams:
            out.append(Issue("error", "L-MAT-002", f, f"{m.code}: family {m.family!r} not listed"))
        bad = set(m.designations) - systems
        if bad:
            out.append(
                Issue(
                    "error",
                    "L-MAT-003",
                    f,
                    f"{m.code}: designation system(s) {sorted(bad)} not in designation_systems",
                )
            )

    roots = {c.code.split(".")[0] for c in lib.taxonomy.classes}
    for pname, p in lib.profiles.items():
        for key, d in p.defaults.items():
            if d.material not in codes:
                out.append(
                    Issue(
                        "error", "L-PRO-001", pname, f"defaults.{key}: unknown material {d.material}"
                    )
                )
        prefixes = p.numbering.prefixes
        for root in sorted(roots):
            if root not in prefixes:
                out.append(
                    Issue(
                        "warning",
                        "L-PRO-002",
                        pname,
                        f"no numbering prefix for class family {root!r}; 'default' is used",
                    )
                )
        for key in prefixes:
            if key != "default" and key not in roots:
                out.append(
                    Issue("error", "L-PRO-003", pname, f"numbering prefix for unknown family {key!r}")
                )
        if "default" not in prefixes:
            out.append(Issue("error", "L-PRO-004", pname, "numbering.prefixes needs a 'default'"))
    if "profiles/default.yaml" not in lib.profiles:
        out.append(Issue("error", "L-PRO-005", "profiles", "profiles/default.yaml is required"))
    return out


# --- units -------------------------------------------------------------------------------------


def _check_units(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[str, str] = {}
    for u in lib.units.units:
        for a in [u.code, *u.aliases]:
            k = norm_token(a)
            if k in seen and seen[k] != u.code:
                out.append(
                    Issue(
                        "error", "L-UNI-001", "units.yaml", f"alias {a!r} maps to {seen[k]} and {u.code}"
                    )
                )
            seen[k] = u.code
    return out


# --- standard tables ---------------------------------------------------------------------------


def _check_standards(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    codes = class_codes(lib)
    keys: dict[str, str] = {}
    for rel, t in lib.standards.items():
        if t.key in keys:
            out.append(Issue("error", "L-STD-001", rel, f"duplicate key {t.key} (also {keys[t.key]})"))
        keys[t.key] = rel
        if t.class_ not in codes:
            out.append(Issue("error", "L-STD-002", rel, f"class {t.class_} not in taxonomy"))
        out += _check_one_table(rel, t)
    return out


def _check_one_table(rel: str, t: StandardTable) -> list[Issue]:
    out: list[Issue] = []
    nominal = t.dims[0]
    for k in t.tolerance_mm:
        if k not in t.dims:
            out.append(Issue("error", "L-STD-003", rel, f"tolerance for unknown dim {k!r}"))
    for k in t.monotonic:
        if k not in t.dims:
            out.append(Issue("error", "L-STD-003", rel, f"monotonic dim {k!r} not in dims"))
    if t.group_by and t.group_by not in t.dims and any(t.group_by not in r for r in t.sizes):
        out.append(Issue("error", "L-STD-003", rel, f"group_by {t.group_by!r} missing in rows"))

    names: set[str] = set()
    for i, row in enumerate(t.sizes):
        label = str(row.get("size", f"row {i}"))
        if "size" not in row:
            out.append(Issue("error", "L-STD-004", rel, f"row {i} has no 'size'"))
            continue
        if label in names:
            out.append(Issue("error", "L-STD-004", rel, f"duplicate size {label}"))
        names.add(label)
        for d in t.dims:
            v = row.get(d)
            if not isinstance(v, int | float) or isinstance(v, bool):
                out.append(Issue("error", "L-STD-005", rel, f"{label}: dim {d!r} missing or not numeric"))
            elif v <= 0:
                out.append(Issue("error", "L-STD-005", rel, f"{label}: dim {d!r} must be positive"))
        m = _SIZE_NUM_RE.match(label)
        nom = row.get("d")  # thread diameter column, present on screws and nuts
        if m and isinstance(nom, int | float) and abs(float(m.group(1)) - float(nom)) > 1e-9:
            out.append(Issue("error", "L-STD-006", rel, f"{label}: label does not match d={nom}"))
        lo, hi = row.get("l_min"), row.get("l_max")
        if lo is not None and hi is not None:
            if not isinstance(lo, int | float) or not isinstance(hi, int | float) or lo >= hi:
                out.append(Issue("error", "L-STD-007", rel, f"{label}: l_min must be below l_max"))
            elif t.lengths_mm and (lo not in t.lengths_mm or hi not in t.lengths_mm):
                out.append(
                    Issue(
                        "error", "L-STD-007", rel, f"{label}: l_min/l_max are not in lengths_mm"
                    )
                )

    # Monotonic inside each group (the whole table when group_by is unset).
    groups: dict[object, list[dict[str, str | float | int]]] = defaultdict(list)
    for row in t.sizes:
        groups[row.get(t.group_by) if t.group_by else None].append(row)
    for g, rows in groups.items():
        gname = f" (group {t.group_by}={g})" if t.group_by else ""
        prev: dict[str, float] = {}
        for row in rows:
            for d in [nominal, *t.monotonic]:
                v = row.get(d)
                if not isinstance(v, int | float):
                    continue
                p = prev.get(d)
                strict = d == nominal
                if p is not None and (v <= p if strict else v < p):
                    out.append(
                        Issue(
                            "error",
                            "L-STD-008",
                            rel,
                            f"{row.get('size')}: {d}={v} is not "
                            f"{'increasing' if strict else 'non-decreasing'} after {p}{gname}",
                        )
                    )
                prev[d] = float(v)

    if t.lengths_mm:
        ls = t.lengths_mm
        if any(b <= a for a, b in zip(ls, ls[1:], strict=False)):
            out.append(Issue("error", "L-STD-009", rel, "lengths_mm is not strictly ascending"))
    missing = [s for s in t.expected_sizes if s not in names]
    if missing:
        out.append(Issue("error", "L-STD-010", rel, f"expected sizes missing: {missing}"))
    return out


# --- synonyms ----------------------------------------------------------------------------------


def synonym_collisions(lib: Library) -> dict[str, list[tuple[str, str]]]:
    """token -> [(class, file)] for tokens that point at 2+ classes with no context rule."""
    by_token: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for rel, sf in lib.synonyms.items():
        for e in sf.entries:
            for tok in e.tokens:
                by_token[norm_token(tok)].append((e.class_, rel, e.context is not None))
    out: dict[str, list[tuple[str, str]]] = {}
    for tok, rows in sorted(by_token.items()):
        plain = {(c, f) for c, f, ctx in rows if not ctx}
        if len({c for c, _ in plain}) > 1:
            out[tok] = sorted(plain)
    return out


def _check_synonyms(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    codes = class_codes(lib)
    pat_ids: set[str] = set()
    for rel, sf in lib.synonyms.items():
        for e in sf.entries:
            if e.class_ not in codes:
                out.append(Issue("error", "L-SYN-002", rel, f"{e.tokens}: class {e.class_} unknown"))
        for p in sf.patterns:
            if p.id in pat_ids:
                out.append(Issue("error", "L-SYN-003", rel, f"duplicate pattern id {p.id}"))
            pat_ids.add(p.id)
            m = re.search(p.regex, p.example)
            if not m:
                out.append(Issue("error", "L-SYN-004", rel, f"pattern {p.id} does not match its example"))
            else:
                for name, idx in p.yields.items():
                    if idx > (m.re.groups or 0):
                        out.append(
                            Issue("error", "L-SYN-004", rel, f"pattern {p.id}: group {idx} ({name}) absent")
                        )
        for v in sf.prefixes:
            if not re.search(v.regex, v.example):
                out.append(Issue("error", "L-SYN-004", rel, f"prefix {v.vendor} does not match its example"))
    for tok, rows in synonym_collisions(lib).items():
        where = ", ".join(f"{c} ({f})" for c, f in rows)
        out.append(
            Issue("warning", "L-SYN-001", "synonyms", f"collision on {tok!r} -> {where}")
        )
    return out


# --- rules -------------------------------------------------------------------------------------


def _check_rules(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    codes = class_refs(lib)
    mats = {m.code for m in lib.materials.materials}
    seen: dict[str, str] = {}
    for rel, rf in lib.rules.items():
        for r in rf.rules:
            if r.id in seen:
                out.append(Issue("error", "L-RUL-001", rel, f"rule id {r.id} also in {seen[r.id]}"))
            seen[r.id] = rel
            m = _RULE_PREFIX_RE.match(r.id)
            if m and m.group(1) != rf.id_prefix:
                out.append(
                    Issue("error", "L-RUL-002", rel, f"{r.id} does not start with {rf.id_prefix}")
                )
            if r.stage != rf.stage:
                out.append(Issue("error", "L-RUL-003", rel, f"{r.id}: stage {r.stage} != file {rf.stage}"))
            refs = _walk(r.when, {"class_is", "parent_class_is", "product_has_class", "not_has_class"})
            refs += _walk(r.then, {"class"})
            for ref in refs:
                if ref not in codes:
                    out.append(Issue("error", "L-RUL-004", rel, f"{r.id}: class {ref} not in taxonomy"))
            for ref in _walk(r.when, {"material_in"}) + _walk(r.then, {"material"}):
                if ref not in mats:
                    out.append(Issue("error", "L-RUL-005", rel, f"{r.id}: material {ref} unknown"))
            for rx in _walk(r.when, {"name_matches"}):
                try:
                    re.compile(rx)
                except re.error as exc:
                    out.append(Issue("error", "L-RUL-006", rel, f"{r.id}: bad regex {rx!r}: {exc}"))
    return out


# --- archetypes, headers -----------------------------------------------------------------------


def _check_archetypes(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    codes = class_codes(lib)
    for rel, a in lib.archetypes.items():
        for item in a.expected:
            if item.class_ not in codes:
                out.append(Issue("error", "L-ARC-001", rel, f"class {item.class_} not in taxonomy"))
    return out


def _check_headers(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    fields = lib.headers.fields
    for must in ("quantity", "part_number", "description"):
        if must not in fields:
            out.append(Issue("error", "L-HDR-001", "headers.yaml", f"field {must!r} is required"))
    seen: dict[str, str] = {}
    for name, f in fields.items():
        for hp in f.header_patterns:
            sample = re.sub(r"[\^\$\\?]", "", hp.regex)  # no example text; just detect duplicates
            if sample in seen and seen[sample] != name:
                out.append(
                    Issue(
                        "warning",
                        "L-HDR-002",
                        "headers.yaml",
                        f"identical header regex under {seen[sample]} and {name}: {hp.regex}",
                    )
                )
            seen[sample] = name
    return out


# --- verify summary ----------------------------------------------------------------------------


def _check_verify_summary(lib: Library) -> list[Issue]:
    out: list[Issue] = []
    std_v = sorted(rel for rel, t in lib.standards.items() if t.verify)
    mat_v = sum(1 for m in lib.materials.materials if m.verify)
    arc_v = sorted(rel for rel, a in lib.archetypes.items() if a.verify)
    out.append(
        Issue(
            "info",
            "L-VER-001",
            "summary",
            f"verify:true -> {len(std_v)}/{len(lib.standards)} standard tables, "
            f"{mat_v}/{len(lib.materials.materials)} materials, "
            f"{len(arc_v)}/{len(lib.archetypes)} archetypes",
        )
    )
    return out
