"""V8 (A5-9): the flags of the old way into the review are not in the parsers of a delivery of cases; the development build keeps them."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile

ROOT = Path(__file__).resolve().parents[1]
GONE = ("--candidate-receipt", "--assembly-receipt", "--inventory-receipt", "--context-receipt", "--context-marker-output", "--generator-fragment",
        "--csv-profile")


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("a5flags") / "pack", "cases-only")


def _help(pack: Path, module: str, *argv: str) -> str:
    done = subprocess.run([sys.executable, "-m", module, *argv, "--help"], cwd=pack, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize("command", ("review-open", "review-select", "review-publish"))
def test_the_review_commands_of_the_delivery_of_cases_have_none_of_the_flags_of_the_old_way(cases_only_pack: Path, command: str) -> None:
    text = _help(cases_only_pack, "tools.api", command)
    assert [flag for flag in GONE if flag in text] == []
    assert "--output-dir" in text and "--run-root" in text


def test_scan_of_the_delivery_of_cases_has_no_disposition_policy(cases_only_pack: Path) -> None:
    assert "--disposition-policy" not in _help(cases_only_pack, "tools.run_pipeline", "scan")


def test_the_development_build_keeps_them() -> None:
    from tools import api

    flags = {spec if isinstance(spec, str) else spec[0] for spec in api.COMMANDS["review-open"][1]}
    assert set(GONE) <= flags
    assert "--disposition-policy" in _help(ROOT, "tools.run_pipeline", "scan")
