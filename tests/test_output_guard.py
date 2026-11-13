"""SEC-A: ``--output-dir`` is ``<run>/published`` of an existing run or below it (tests/test_sec_a.py); ``--out`` is gone since V5-B3 and no tool takes the flags."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path) -> tuple[Path, Path, str]:
    from tests.test_self_review import _run as make_run

    run_root, attempt_id = make_run(tmp_path, "cases-only-v1", flag=False)
    return tmp_path, run_root, attempt_id


def _api(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from tools.api import main

    code = main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")


# ---- --output-dir ------------------------------------------------------------------------------------------------

def test_output_dir_through_a_link_out_of_the_published_directory_is_refused(tmp_path: Path) -> None:
    from tools.output_guard import OutputRefused, run_output_directory

    project, run_root, _attempt = _run(tmp_path / "p")
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_root / "published").mkdir()
    _link(run_root / "published" / "out-link", outside)
    _link(run_root / "published" / "tmp-link", run_root / "tmp")
    for name in ("out-link", "out-link/x", "tmp-link"):
        with pytest.raises(OutputRefused):
            run_output_directory(run_root / "published" / name, run_root)


# ---- every entry point -------------------------------------------------------------------------------------------

FLAGS = {"--out", "--output-dir"}


def test_no_tool_takes_the_flags_of_a_written_path_any_more() -> None:
    """V5-B3: ``--out`` went with the commands of ``tools.api``, ``--output-dir`` with the old review (V4b): the agent writes only the slot of a stage."""
    found = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tools").glob("*.py")
                   for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.value in FLAGS or node.value.startswith("--out-")))
    assert found == [], "a new --out / --output-dir needs tools.output_guard and a test that reads its source"


def test_the_removed_publish_parser_left_no_unchecked_output_dir() -> None:
    assert "--output-dir" not in (ROOT / "tools" / "publish_test_case_bundle.py").read_text(encoding="utf-8")
