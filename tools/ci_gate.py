#!/usr/bin/env python3
"""Run the deterministic local integrity gate (development tool: no delivery carries it).

Without flags it is the gate of the repository: the contract check, the rendered documents, the manifest and the package tests.
``--pack-only`` is the gate of development for a *pack* — a delivery folder or a ``git archive`` — without pytest: the same three
checks plus ``doctor``, run with the tools of this repository against the files of ``--root``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

from tools.json_cli import JsonArgumentParser

REPOSITORY = Path(__file__).resolve().parents[1]


def _checks(root: Path, pack_only: bool) -> tuple[tuple[str, list[str]], ...]:
    modules = (
        ("tools/contract_check.py", ["-m", "tools.contract_check", "--root", str(root), "--full"]),
        ("tools/render_contract_docs.py", ["-m", "tools.render_contract_docs", "--root", str(root), "--check"]),
        ("tools/release_manifest.py", ["-m", "tools.release_manifest", "--root", str(root), "--check"]),
        ("tools/doctor.py", ["-m", "tools.doctor", "--root", str(root)]),
    )
    if pack_only:
        return tuple((path, [sys.executable, *arguments]) for path, arguments in modules)
    return (*((path, [sys.executable, *arguments]) for path, arguments in modules[:2]), ("tests", [sys.executable, "-m", "pytest", "-q"]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Portable pack root")
    parser.add_argument("--pack-only", action="store_true", help="Check a pack (delivery or git archive) with this repository's tools; no pytest")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    # the tools run from this repository (a pack of cases has no ci_gate and no contract_check); --root is only data
    cwd = REPOSITORY if args.pack_only else root
    for required_path, command in _checks(root, args.pack_only):
        if required_path and not (cwd / required_path).exists():
            print(f"NOT_RUNNABLE: missing {required_path}", file=sys.stderr)
            return 2
        try:
            result = subprocess.run(command, cwd=cwd, check=False)
        except OSError as error:
            print(f"NOT_RUNNABLE: {error}", file=sys.stderr)
            return 2
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
