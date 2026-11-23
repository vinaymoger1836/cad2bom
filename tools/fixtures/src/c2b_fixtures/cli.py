"""`c2b-fixtures build`: generate the golden cases from the library tables."""

import argparse
import sys
from pathlib import Path

from c2b_fixtures.build import ALL_CASES, build, import_real_fan, write_readme
from c2b_library.cli import validate_library


def _find_library() -> Path:
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), *here.parents]:
        if (parent / "library" / "VERSION.yaml").is_file():
            return parent / "library"
    return Path("library")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="c2b-fixtures", description="Synthetic CAD and BoM fixtures.")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate golden inputs and expected outputs")
    b.add_argument("--out", type=Path, default=Path("golden"))
    b.add_argument("--library", type=Path, default=_find_library())
    b.add_argument("--case", default=",".join(ALL_CASES), help="comma list, e.g. B1,B2,B6")
    f = sub.add_parser("import-fan", help="copy the owner's real fan STEP into B3 as fan_real.step")
    f.add_argument("src", type=Path)
    f.add_argument("--out", type=Path, default=Path("golden"))
    f.add_argument("--library", type=Path, default=_find_library())
    args = p.parse_args(argv)

    lib, issues = validate_library(args.library)
    if lib is None or any(i.level == "error" for i in issues):
        for i in issues:
            if i.level == "error":
                print(i, file=sys.stderr)
        print("library is invalid: run `c2b-library validate`", file=sys.stderr)
        return 1
    if args.cmd == "import-fan":
        dest = import_real_fan(lib, args.src, args.out)
        print(f"wrote {dest}  ({dest.stat().st_size} bytes)")
        return 0
    cases = [c.strip().upper() for c in args.case.split(",") if c.strip()]
    unknown = [c for c in cases if c not in ALL_CASES]
    if unknown:
        print(f"unknown case(s): {unknown}", file=sys.stderr)
        return 2
    files = build(lib, args.out, cases)
    if set(cases) == set(ALL_CASES):
        write_readme(args.out, lib)
    for out_path in files:
        print(f"wrote {out_path}  ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
