"""The ``papereyes`` command line.

Stage 0-1 surface (design spec §2 IN, §6):

- ``papereyes version`` — print the version.
- ``papereyes check [TARGET]`` — load a formpack dir (or a ``pipeline.yaml``) as data and
  report whether it is well-formed. Exit 0 on OK, 1 on a malformed config.
- ``papereyes init DIR [--name NAME]`` — scaffold a new formpack (formpack.yaml + schema.json
  + golden/) as data, valid on creation.

The pipeline commands (``run``, ``gate``, ``watch``) land in later stages; they are not
wired here so nothing advertises a capability that does not yet exist.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from papereyes import __version__
from papereyes.errors import PaperEyesError
from papereyes.formpack.check import check_target
from papereyes.formpack.scaffold import scaffold_formpack


def _cmd_version() -> int:
    print(f"papereyes {__version__}")
    return 0


def _cmd_check(target: str | None) -> int:
    resolved = target or "pipeline.yaml"
    report = check_target(resolved)
    print(report.render(), file=sys.stdout if report.ok else sys.stderr)
    return 0 if report.ok else 1


def _cmd_init(dest: str, name: str | None) -> int:
    formpack_name = name or Path(dest).name or "my-formpack"
    try:
        written = scaffold_formpack(dest, name=formpack_name)
    except (PaperEyesError, OSError) as exc:
        print(f"init FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"scaffolded formpack {formpack_name!r} in {dest}:")
    for path in written:
        print(f"    {path}")
    print("next:")
    print(f"    1. edit the TODOs in {dest}/formpack.yaml and {dest}/schema.json")
    print(f"    2. papereyes check {dest}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="papereyes",
        description="papereyes — scanned public-form intake into deterministic JSON.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the papereyes version")

    check = sub.add_parser("check", help="validate a formpack dir or a pipeline.yaml as data")
    check.add_argument(
        "target",
        nargs="?",
        default=None,
        help="a formpack directory or a pipeline.yaml (default: ./pipeline.yaml)",
    )

    init = sub.add_parser("init", help="scaffold a new formpack (formpack.yaml + schema.json)")
    init.add_argument("dir", help="destination directory for the new formpack")
    init.add_argument(
        "--name", default=None, help="the new formpack's name (default: the dir's name)"
    )

    args = parser.parse_args(argv)
    if args.command == "version":
        return _cmd_version()
    if args.command == "check":
        return _cmd_check(args.target)
    if args.command == "init":
        return _cmd_init(args.dir, args.name)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
