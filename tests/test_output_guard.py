"""P8a-2: ``--out`` is one file in ``<run>/tmp/`` of an existing run; SEC-A: ``--output-dir`` is ``<run>/published`` of that run or
below it (tests/test_sec_a.py).  Every entry point of the package that takes one of the flags checks it."""
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


# ---- --out -------------------------------------------------------------------------------------------------------

def test_out_is_a_file_directly_in_the_tmp_of_the_run(tmp_path: Path) -> None:
    from tools.output_guard import out_file

    _project, run_root, _attempt = _run(tmp_path)
    assert out_file(run_root / "tmp" / "x.json", run_root) == (run_root / "tmp" / "x.json").resolve()


@pytest.mark.parametrize("name", ["x.json", "tmp/sub/x.json", "tmp/../x.json", "tmp", "../tmp/x.json", "other-run/tmp/x.json"])
def test_out_anywhere_else_is_refused_with_its_code_and_a_fix(tmp_path: Path, name: str) -> None:
    from tools.output_guard import OutputRefused, out_file

    _project, run_root, _attempt = _run(tmp_path)
    with pytest.raises(OutputRefused) as refused:
        out_file(run_root / name, run_root)
    assert refused.value.code == "API_OUT_OUTSIDE_RUN" and "--run-root" in refused.value.fix


def test_out_of_another_run_of_the_project_is_refused(tmp_path: Path) -> None:
    from tools.output_guard import OutputRefused, out_file

    project, run_root, _attempt = _run(tmp_path)
    other = project / ".pilot-runs" / ("f" * 32)
    (other / "tmp").mkdir(parents=True)
    (other / "run-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(OutputRefused):
        out_file(other / "tmp" / "x.json", run_root)


def test_out_without_run_root_or_with_a_run_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    from tools.output_guard import OutputRefused, out_file

    project, run_root, _attempt = _run(tmp_path)
    with pytest.raises(OutputRefused, match="--run-root is missing"):
        out_file(run_root / "tmp" / "x.json", None)
    fake = project / ".pilot-runs" / ("e" * 32)
    (fake / "tmp").mkdir(parents=True)  # a directory without run-manifest.json is no run
    with pytest.raises(OutputRefused, match="existing run"):
        out_file(fake / "tmp" / "x.json", fake)
    with pytest.raises(OutputRefused):
        out_file(project / "x.json", project)


def test_out_through_a_linked_tmp_is_refused(tmp_path: Path) -> None:
    from tools.output_guard import OutputRefused, out_file

    project, run_root, _attempt = _run(tmp_path / "p")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = run_root / "linked-tmp"
    _link(link, elsewhere)
    (run_root / "tmp").rmdir() if (run_root / "tmp").is_dir() and not any((run_root / "tmp").iterdir()) else None
    if (run_root / "tmp").exists():
        pytest.skip("tmp/ is in use")
    os.replace(link, run_root / "tmp")
    with pytest.raises(OutputRefused):
        out_file(run_root / "tmp" / "x.json", run_root)


def test_the_cli_takes_run_root_for_a_command_that_has_no_run_of_its_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project, run_root, _attempt = _run(tmp_path)
    source = run_root / "tmp" / "document.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text("{}", encoding="utf-8")
    out = run_root / "tmp" / "digest.json"
    code, refused = _api(["relations-digest", "--relations", str(source), "--out", str(out)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN" and "--run-root" in refused["fix"] and not out.exists(), refused
    code, line = _api(["relations-digest", "--relations", str(source), "--out", str(out), "--run-root", str(run_root)], capsys)
    assert code == 0 and out.is_file() and line["out"] == str(out.resolve()), line
    code, refused = _api(["relations-digest", "--relations", str(source), "--out", str(run_root / "d.json"), "--run-root", str(run_root)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN", refused


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

FLAGS = {"--out"}
ENTRY_POINTS = {"tools/api.py"}  # V4b: the one CLI that takes the flag; ``--output-dir`` has no command any more


def _flag_files() -> dict[str, ast.Module]:
    found: dict[str, ast.Module] = {}
    for path in sorted((ROOT / "tools").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.value in FLAGS or node.value.startswith("--out-")):
                found[path.relative_to(ROOT).as_posix()] = tree
    return found


def test_only_the_known_entry_points_take_the_flags_and_each_checks_them() -> None:
    found = _flag_files()
    assert set(found) == ENTRY_POINTS, "a new --out / --output-dir needs tools.output_guard and a place in ENTRY_POINTS"
    for relative, tree in found.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "_out_target(" in text or "out_file(" in text, relative


def test_the_removed_publish_parser_left_no_unchecked_output_dir() -> None:
    assert "--output-dir" not in (ROOT / "tools" / "publish_test_case_bundle.py").read_text(encoding="utf-8")
