"""Offline search over the YAML files (used by `c2b-library search --files`, no database needed)."""

from difflib import SequenceMatcher
from typing import Any

from c2b_library.checks import norm_token
from c2b_library.loader import Library


def _score(q: str, text: str) -> float:
    t = norm_token(text)
    if q in t:
        return 0.6 + 0.4 * (len(q) / max(len(t), 1))
    return SequenceMatcher(None, q, t).ratio() * 0.55


def search_files(lib: Library, query: str, limit: int = 15) -> list[dict[str, Any]]:
    q = norm_token(query)
    hits: list[dict[str, Any]] = []
    for c in lib.taxonomy.classes:
        hits.append(
            {"kind": "class", "key": c.code, "label": c.noun, "score": _score(q, f"{c.code} {c.noun}")}
        )
    for sf in lib.synonyms.values():
        for e in sf.entries:
            for tok in e.tokens:
                hits.append({"kind": "synonym", "key": norm_token(tok), "label": e.class_, "score": _score(q, tok)})
    for m in lib.materials.materials:
        desig = " ".join(d for ds in m.designations.values() for d in ds)
        hits.append(
            {
                "kind": "material",
                "key": m.code,
                "label": m.display,
                "score": max(_score(q, f"{m.code} {m.display}"), _score(q, desig) if q in norm_token(desig) else 0),
            }
        )
    for t in lib.standards.values():
        hits.append({"kind": "standard", "key": t.key, "label": t.standard, "score": _score(q, f"{t.key} {t.standard}")})
    hits = [h for h in hits if h["score"] > 0.25]
    hits.sort(key=lambda h: (-h["score"], h["kind"], h["key"]))
    for h in hits:
        h["score"] = round(h["score"], 3)
        h["version"] = lib.version.version
    return hits[:limit]
