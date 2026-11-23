"""`c2b-library validate | load | search` (docs/06 §8)."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

from c2b_library.checks import run_checks, synonym_collisions
from c2b_library.db import VersionConflict, expected_counts, search_db, store
from c2b_library.loader import Issue, Library, load_library
from c2b_library.search import search_files

DEFAULT_PATH = Path("library")


def _find_default_path() -> Path:
    """`library/` in the current directory, else next to the repository root (3 levels up)."""
    if DEFAULT_PATH.is_dir():
        return DEFAULT_PATH
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "library" / "VERSION.yaml").is_file():
            return parent / "library"
    return DEFAULT_PATH


def validate_library(path: Path) -> tuple[Library | None, list[Issue]]:
    lib, issues = load_library(path)
    if lib is not None:
        issues = issues + run_checks(lib)
    return lib, issues


def _print_issues(issues: list[Issue], as_json: bool, lib: Library | None) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "version": lib.version.version if lib else None,
                    "errors": sum(i.level == "error" for i in issues),
                    "warnings": sum(i.level == "warning" for i in issues),
                    "issues": [i.__dict__ for i in issues],
                    "synonym_collisions": (
                        {t: [list(x) for x in rows] for t, rows in synonym_collisions(lib).items()}
                        if lib
                        else {}
                    ),
                },
                indent=2,
            )
        )
        return
    for level in ("error", "warning", "info"):
        for i in issues:
            if i.level == level:
                print(i)
    if lib is not None:
        n_std = len(lib.standards)
        n_rows = sum(len(t.sizes) for t in lib.standards.values())
        counts = expected_counts(lib)
        print(
            f"\nlibrary {lib.version.version}: {counts['library_class']} classes, "
            f"{counts['library_material']} materials, {n_std} standard tables ({n_rows} rows), "
            f"{counts['library_synonym']} synonym tokens, {counts['library_rule']} rules, "
            f"{len(lib.archetypes)} archetypes, checksum {lib.checksum[:12]}"
        )
    errs = sum(i.level == "error" for i in issues)
    warns = sum(i.level == "warning" for i in issues)
    print(f"{'FAILED' if errs else 'OK'}: {errs} error(s), {warns} warning(s)")


def cmd_validate(args: argparse.Namespace) -> int:
    lib, issues = validate_library(args.path)
    _print_issues(issues, args.json, lib)
    return 1 if any(i.level == "error" for i in issues) else 0


def _db_url(args: argparse.Namespace) -> str:
    url: str | None = args.database_url or os.environ.get("C2B_DATABASE_URL")
    if not url:
        print("error: no database. Pass --database-url or set C2B_DATABASE_URL.", file=sys.stderr)
        raise SystemExit(2)
    return url


def cmd_load(args: argparse.Namespace) -> int:
    lib, issues = validate_library(args.path)
    if lib is None or any(i.level == "error" for i in issues):
        _print_issues(issues, False, lib)
        print("not loaded: fix the errors above first", file=sys.stderr)
        return 1
    url = _db_url(args)
    if args.migrate:
        from c2b_jobs.migrate import run_migrations

        run_migrations(url)
    try:
        with psycopg.connect(url) as conn:
            result = store(lib, conn)
    except VersionConflict as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except psycopg.errors.UndefinedTable:
        print("error: library tables are missing. Run with --migrate or start the api once.", file=sys.stderr)
        return 2
    print(f"{result.status}: library {result.version} " + json.dumps(result.counts, sort_keys=True))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    hits: list[dict[str, Any]]
    if args.files:
        lib, issues = load_library(args.path)
        if lib is None:
            _print_issues(issues, False, None)
            return 1
        hits = search_files(lib, args.query, args.limit)
    else:
        with psycopg.connect(_db_url(args)) as conn:
            hits = search_db(conn, args.query, args.version, args.limit)
    if args.json:
        print(json.dumps(hits, indent=2))
    elif not hits:
        print("no matches")
    else:
        for h in hits:
            print(f"{h['score']:.2f}  {h['kind']:<9} {h['key']:<42} {h['label']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="c2b-library", description="Validate, load and search library/ YAML.")
    sub = p.add_subparsers(dest="cmd", required=True)
    default_path = _find_default_path()

    v = sub.add_parser("validate", help="schema and consistency checks (no database)")
    v.add_argument("--path", type=Path, default=default_path)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_validate)

    ld = sub.add_parser("load", help="validate, then load one version into Postgres")
    ld.add_argument("--path", type=Path, default=default_path)
    ld.add_argument("--database-url")
    ld.add_argument("--migrate", action="store_true", help="apply pending SQL migrations first")
    ld.set_defaults(func=cmd_load)

    s = sub.add_parser("search", help="search classes, synonyms, materials and standards")
    s.add_argument("query")
    s.add_argument("--path", type=Path, default=default_path)
    s.add_argument("--files", action="store_true", help="search the YAML files, not the database")
    s.add_argument("--database-url")
    s.add_argument("--version")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
