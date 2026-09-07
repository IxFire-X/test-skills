#!/usr/bin/env python3
"""Run the deterministic local integrity gate."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Portable pack root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    checks = (
        ("tools/contract_check.py", [sys.executable, "-m", "tools.contract_check", "--root", str(root), "--full"]),
        ("tools/render_contract_docs.py", [sys.executable, "-m", "tools.render_contract_docs", "--root", str(root), "--check"]),
        (None, [sys.executable, "-m", "pytest", "-q"]),
    )
    for required_path, command in checks:
        if required_path and not (root / required_path).is_file():
            print(f"NOT_RUNNABLE: missing {required_path}", file=sys.stderr)
            return 2
        try:
            result = subprocess.run(command, cwd=root, check=False)
        except OSError as error:
            print(f"NOT_RUNNABLE: {error}", file=sys.stderr)
            return 2
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
