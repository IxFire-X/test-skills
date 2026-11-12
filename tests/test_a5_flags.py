"""V8 (A5-9): ``scan`` of the delivery of cases has no disposition policy; the development build keeps it."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("a5flags") / "pack", "cases-only")


def _help(pack: Path, module: str, *argv: str) -> str:
    done = subprocess.run([sys.executable, "-m", module, *argv, "--help"], cwd=pack, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_scan_of_the_delivery_of_cases_has_no_disposition_policy(cases_only_pack: Path) -> None:
    assert "--disposition-policy" not in _help(cases_only_pack, "tools.run_pipeline", "scan")


def test_the_development_build_keeps_the_disposition_policy() -> None:
    assert "--disposition-policy" in _help(ROOT, "tools.run_pipeline", "scan")
