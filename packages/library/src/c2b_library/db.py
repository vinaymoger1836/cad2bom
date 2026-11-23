"""Load a validated library into Postgres and search it."""

import json
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from c2b_library.checks import norm_token
from c2b_library.loader import Library


class VersionConflict(RuntimeError):
    """The version is already loaded with different content. Bump VERSION.yaml instead."""


@dataclass(frozen=True)
class LoadResult:
    status: str  # "loaded" | "unchanged"
    version: str
    counts: dict[str, int]


def _dump(model: Any) -> Any:
    return json.loads(model.model_dump_json(by_alias=True))


def expected_counts(lib: Library) -> dict[str, int]:
    return {
        "library_class": len(lib.taxonomy.classes),
        "library_material": len(lib.materials.materials),
        "library_standard": len(lib.standards),
        "library_synonym": sum(len(t.tokens) for sf in lib.synonyms.values() for t in sf.entries),
        "library_rule": sum(len(rf.rules) for rf in lib.rules.values()),
    }


def _counts(conn: psycopg.Connection[Any], version: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in (
        "library_class",
        "library_material",
        "library_standard",
        "library_synonym",
        "library_rule",
    ):
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE version = %s",  # noqa: S608
            (version,),
        ).fetchone()
        out[table] = int(row[0]) if row else 0
    return out


def store(lib: Library, conn: psycopg.Connection[Any]) -> LoadResult:
    """Write the library as one version in a single transaction. Idempotent for equal content."""
    v = lib.version.version
    with conn.transaction():
        row = conn.execute(
            "SELECT checksum FROM library_version WHERE version = %s", (v,)
        ).fetchone()
        if row is not None:
            if row[0] == lib.checksum:
                return LoadResult("unchanged", v, _counts(conn, v))
            raise VersionConflict(
                f"library version {v} is already loaded with different content "
                f"(stored {row[0][:12]}…, files {lib.checksum[:12]}…). Bump VERSION.yaml."
            )
        conn.execute(
            "INSERT INTO library_version (version, checksum, released, changelog, file_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            (v, lib.checksum, lib.version.date, Jsonb(lib.version.changelog), lib.file_count),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO library_class (version, code, parent, noun, modifiers, uom, "
                "make_buy_default, usually_modelled, buy_as_assembly, description_template, "
                "standard_families, material_default_profile_key, unspsc, note) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        v,
                        c.code,
                        c.parent,
                        c.noun,
                        Jsonb(c.modifiers),
                        c.uom,
                        c.make_buy_default,
                        c.usually_modelled,
                        c.buy_as_assembly,
                        c.description_template,
                        Jsonb(c.standard_families),
                        c.material_default_profile_key,
                        c.unspsc,
                        c.note,
                    )
                    for c in lib.taxonomy.resolved()
                ],
            )
            cur.executemany(
                "INSERT INTO library_material (version, code, family, display, density_g_cm3, "
                "designations, body) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        v,
                        m.code,
                        m.family,
                        m.display,
                        m.density_g_cm3,
                        Jsonb(m.designations),
                        Jsonb(_dump(m)),
                    )
                    for m in lib.materials.materials
                ],
            )
            cur.executemany(
                "INSERT INTO library_standard (version, key, standard, class, verify, body) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                [
                    (v, t.key, t.standard, t.class_, t.verify, Jsonb(_dump(t)))
                    for t in lib.standards.values()
                ],
            )
            cur.executemany(
                "INSERT INTO library_synonym "
                "(version, token, class, lang, weight, kind, context, file) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        v,
                        norm_token(tok),
                        e.class_,
                        sf.lang,
                        e.weight,
                        e.kind,
                        Jsonb(e.context) if e.context else None,
                        rel,
                    )
                    for rel, sf in lib.synonyms.items()
                    for e in sf.entries
                    for tok in e.tokens
                ],
            )
            cur.executemany(
                "INSERT INTO library_rule (version, id, stage, weight, note, body) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                [
                    (v, r.id, r.stage, r.weight, r.note, Jsonb({"when": r.when, "then": r.then}))
                    for rf in lib.rules.values()
                    for r in rf.rules
                ],
            )
            blobs: list[tuple[str, str, str, Any]] = [
                (v, "headers", "headers", Jsonb(_dump(lib.headers))),
                (v, "units", "units", Jsonb(_dump(lib.units))),
                (v, "confidence", "confidence", Jsonb(_dump(lib.confidence))),
            ]
            blobs += [(v, "profile", rel, Jsonb(_dump(p))) for rel, p in lib.profiles.items()]
            blobs += [(v, "archetype", rel, Jsonb(_dump(a))) for rel, a in lib.archetypes.items()]
            for rel, sf in lib.synonyms.items():
                if sf.patterns:
                    blobs.append((v, "size_patterns", rel, Jsonb([_dump(p) for p in sf.patterns])))
                if sf.prefixes:
                    blobs.append(
                        (v, "vendor_prefixes", rel, Jsonb([_dump(p) for p in sf.prefixes]))
                    )
            cur.executemany(
                "INSERT INTO library_blob (version, kind, name, body) VALUES (%s,%s,%s,%s)", blobs
            )
        return LoadResult("loaded", v, _counts(conn, v))


def latest_version(conn: psycopg.Connection[Any]) -> str | None:
    row = conn.execute(
        "SELECT version FROM library_version ORDER BY loaded_at DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


def search_db(
    conn: psycopg.Connection[Any], query: str, version: str | None, limit: int = 15
) -> list[dict[str, Any]]:
    """Trigram + substring search across classes, synonyms, materials and standards."""
    ver = version or latest_version(conn)
    if ver is None:
        return []
    q = norm_token(query)
    like = f"%{q}%"
    sql = """
    SELECT * FROM (
      SELECT 'class' AS kind, code AS key, noun AS label,
             GREATEST(similarity(lower(code || ' ' || noun), %(q)s),
                      CASE WHEN lower(code || ' ' || noun) LIKE %(like)s
                           THEN 0.6 ELSE 0 END) AS score
        FROM library_class WHERE version = %(v)s
      UNION ALL
      SELECT 'synonym', token, class,
             GREATEST(similarity(token, %(q)s), CASE WHEN token LIKE %(like)s THEN 0.6 ELSE 0 END)
        FROM library_synonym WHERE version = %(v)s
      UNION ALL
      SELECT 'material', code, display,
             GREATEST(similarity(lower(code || ' ' || display), %(q)s),
                      CASE WHEN lower(code || ' ' || display || ' '
                                      || designations::text) LIKE %(like)s
                           THEN 0.6 ELSE 0 END)
        FROM library_material WHERE version = %(v)s
      UNION ALL
      SELECT 'standard', key, standard,
             GREATEST(similarity(lower(key || ' ' || standard), %(q)s),
                      CASE WHEN lower(key || ' ' || standard) LIKE %(like)s THEN 0.6 ELSE 0 END)
        FROM library_standard WHERE version = %(v)s
    ) hits WHERE score > 0.25 ORDER BY score DESC, kind, key LIMIT %(n)s
    """
    rows = conn.execute(sql, {"q": q, "like": like, "v": ver, "n": limit}).fetchall()
    return [
        {"kind": r[0], "key": r[1], "label": r[2], "score": round(float(r[3]), 3), "version": ver}
        for r in rows
    ]
