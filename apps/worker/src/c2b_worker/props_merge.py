"""Merge the properties Autodesk returned for a SolidWorks file into CAD nodes (docs/03 §2).

Pure functions, no I/O. Which property name means what comes from `library/cad_props.yaml`; the
material text is matched to a library material code through its designations. Nothing is guessed:
a name that matches no node, or matches several with different values, is reported and skipped.
"""

from __future__ import annotations

import re
from typing import Any

from c2b_library.loader import Library

MAX_CUSTOM = 20
MAX_VALUE = 200

_INSTANCE_SUFFIX = re.compile(r"(<\d+>|:\d+|\[\d+\])$")
_EXT = re.compile(r"\.(sldprt|sldasm|f3d|step|stp)$", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9/]", "", text.lower())


def node_key(name: str) -> str:
    """Name without the instance suffix (`Bracket<2>`) or CAD extension, for matching."""
    n = _EXT.sub("", name.strip())
    n = _INSTANCE_SUFFIX.sub("", n).strip()
    return _norm(n)


def canonical_name(prop: str, library: Library) -> str | None:
    """Our key (part_number, description, material, class) for a CAD property name, if any."""
    last = _norm(prop.split(".")[-1])
    for key, names in library.cad_props.canonical.items():
        if any(last == _norm(n) for n in names):
            return key
    return None


def material_code(text: str, library: Library) -> str | None:
    """Library material code for a material text from CAD, matched on code, display or any
    designation (case and punctuation ignored)."""
    want = _norm(text)
    if not want:
        return None
    for m in library.materials.materials:
        if want in {_norm(m.code), _norm(m.display)}:
            return m.code
        for names in m.designations.values():
            if any(want == _norm(str(n)) for n in names):
                return m.code
    return None


def merge_props(
    nodes: list[dict[str, Any]],
    node_props: list[dict[str, Any]],
    library: Library,
) -> tuple[int, list[dict[str, str]]]:
    """Set `props` on matching nodes (in place). Returns (nodes updated, notices)."""
    ignore = {_norm(v) for v in library.cad_props.ignore_values}
    classes = {c.code for c in library.taxonomy.resolved()}

    # Group the properties by node key; identical duplicates collapse, conflicts are dropped.
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in node_props:
        by_key.setdefault(node_key(str(item.get("name", ""))), []).append(item)

    notices: list[dict[str, str]] = []
    updated = 0
    for node in nodes:
        key = node_key(str(node.get("name", "")))
        candidates = by_key.get(key)
        if not key or not candidates:
            continue
        flat = [c.get("props", {}) for c in candidates]
        if any(f != flat[0] for f in flat[1:]):
            notices.append(
                {
                    "code": "props_ambiguous",
                    "message": (
                        f"'{node.get('name')}' matches several objects with different properties "
                        "in the converted file, so none were used."
                    ),
                    "severity": "warning",
                    "node_id": str(node.get("id", "")),
                }
            )
            continue
        props = _apply(node, flat[0], library, ignore, classes, notices)
        if props:
            node.setdefault("props", {}).update(props)
            updated += 1
    return updated, notices


def _apply(
    node: dict[str, Any],
    raw: dict[str, str],
    library: Library,
    ignore: set[str],
    classes: set[str],
    notices: list[dict[str, str]],
) -> dict[str, str]:
    out: dict[str, str] = {}
    done: set[str] = set()
    custom = 0
    nid = str(node.get("id", ""))
    name = str(node.get("name", ""))
    for prop, value in raw.items():
        value = str(value).strip()
        if _norm(value) in ignore:
            continue
        canon = canonical_name(prop, library)
        if canon is None:
            if "." in prop and _is_custom_group(prop) and custom < MAX_CUSTOM:
                out[f"custom.{prop.split('.', 1)[1]}"] = value[:MAX_VALUE]
                custom += 1
            continue
        if canon in done:  # "Group.Key" and bare "Key" both appear; use the first
            continue
        done.add(canon)
        if canon == "material":
            code = material_code(value, library)
            if code is None:
                out["material_raw"] = value[:MAX_VALUE]
                notices.append(
                    {
                        "code": "material_not_in_library",
                        "message": (
                            f"'{name}' says its material is '{value}', which is not in the "
                            "library, so the rules choose one and you can confirm it."
                        ),
                        "severity": "info",
                        "node_id": nid,
                    }
                )
                continue
            out["material"] = code
        elif canon == "class":
            if value not in classes:
                notices.append(
                    {
                        "code": "class_not_in_library",
                        "message": f"'{name}' has class '{value}', which is not a library class.",
                        "severity": "info",
                        "node_id": nid,
                    }
                )
                continue
            out["class"] = value
        else:
            out[canon] = value[:MAX_VALUE]
    return out


def _is_custom_group(prop: str) -> bool:
    group = prop.split(".", 1)[0].lower()
    return "custom" in group
