#!/usr/bin/env python3
"""Run the deterministic local integrity gate.

Exit codes: 0 — every check passed; 2 — the gate could not run (a required tool
or pytest is missing, a child could not start or timed out); otherwise the exit
code of the first failed check.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Sequence


CHECK_TIMEOUT_SECONDS = 900
PYTEST_TIMEOUT_SECONDS = 4 * 60 * 60


def _positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds")
    return seconds


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Portable pack root")
    parser.add_argument("--check-timeout", type=_positive_seconds, default=CHECK_TIMEOUT_SECONDS, help="Seconds allowed for each contract check")
    parser.add_argument("--pytest-timeout", type=_positive_seconds, default=PYTEST_TIMEOUT_SECONDS, help="Seconds allowed for the test suite")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    checks = (
        ("tools/contract_check.py", None, args.check_timeout, [sys.executable, "-m", "tools.contract_check", "--root", str(root), "--full"]),
        ("tools/render_contract_docs.py", None, args.check_timeout, [sys.executable, "-m", "tools.render_contract_docs", "--root", str(root), "--check"]),
        (None, "pytest", args.pytest_timeout, [sys.executable, "-m", "pytest", "-q"]),
    )
    for required_path, required_module, timeout, command in checks:
        if required_path and not (root / required_path).is_file():
            print(f"NOT_RUNNABLE: missing {required_path}", file=sys.stderr)
            return 2
        # ``python -m <missing module>`` exits 1, which would read as a failed check.
        if required_module and importlib.util.find_spec(required_module) is None:
            print(f"NOT_RUNNABLE: {required_module} is not installed for {sys.executable}", file=sys.stderr)
            return 2
        try:
            result = subprocess.run(command, cwd=root, check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"NOT_RUNNABLE: {' '.join(command[1:3])} timed out after {timeout} seconds", file=sys.stderr)
            return 2
        except OSError as error:
            print(f"NOT_RUNNABLE: {error}", file=sys.stderr)
            return 2
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
