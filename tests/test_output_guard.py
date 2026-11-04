"""P8a-2 (security review 79d4052, unbounded paths): ``--out`` is one file in ``<run>/tmp/`` of an existing run, ``--output-dir``
is a directory inside the project and never inside ``.git``.  Every entry point of the package that takes one of the flags checks it."""
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
    code, refused = _api(["document-digest", "--document", str(source), "--out", str(out)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN" and "--run-root" in refused["fix"] and not out.exists(), refused
    code, line = _api(["document-digest", "--document", str(source), "--out", str(out), "--run-root", str(run_root)], capsys)
    assert code == 0 and out.is_file() and line["out"] == str(out.resolve()), line
    code, refused = _api(["document-digest", "--document", str(source), "--out", str(run_root / "d.json"), "--run-root", str(run_root)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN", refused


# ---- --output-dir ------------------------------------------------------------------------------------------------

def test_output_dir_is_inside_the_project(tmp_path: Path) -> None:
    from tools.output_guard import output_directory

    project = tmp_path / "project"
    project.mkdir()
    assert output_directory(project / "test-cases", project) == (project / "test-cases").resolve()
    assert output_directory(project, project) == project.resolve()


@pytest.mark.parametrize("name", ["../outside", "../project-two/x", ".git", ".git/hooks", "a/.git/hooks", "a/.GIT/x", "sub/../.git/hooks"])
def test_output_dir_outside_the_project_or_inside_git_is_refused(tmp_path: Path, name: str) -> None:
    from tools.output_guard import OutputRefused, output_directory

    project = tmp_path / "project"
    (project / "a").mkdir(parents=True)
    (project / "sub").mkdir()
    with pytest.raises(OutputRefused) as refused:
        output_directory(project / name, project)
    assert refused.value.code == "OUTPUT_DIR_OUTSIDE_PROJECT" and "внутри проекта" in refused.value.fix
    assert not (tmp_path / "outside").exists()


def test_output_dir_through_a_link_out_of_the_project_or_into_git_is_refused(tmp_path: Path) -> None:
    from tools.output_guard import OutputRefused, output_directory

    project, outside = tmp_path / "project", tmp_path / "outside"
    (project / ".git" / "hooks").mkdir(parents=True)
    outside.mkdir()
    _link(project / "out-link", outside)
    _link(project / "git-link", project / ".git")
    for name in ("out-link", "out-link/x", "git-link/hooks"):
        with pytest.raises(OutputRefused):
            output_directory(project / name, project)


def test_the_cli_refuses_an_output_dir_outside_the_project_before_it_reads_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, run_root, attempt_id = _run(tmp_path / "p")
    outside = tmp_path / "outside"
    for argv in (["review-open", "--output-dir", str(outside)], ["review-select", "--output-dir", str(outside)],
                 ["review-publish", "--output-dir", str(outside)], ["review-open", "--output-dir", str(project / ".git" / "hooks")]):
        code, refused = _api([argv[0], "--run-root", str(run_root), "--attempt", attempt_id, *argv[1:]], capsys)
        assert code == 2 and refused["code"] == "OUTPUT_DIR_OUTSIDE_PROJECT" and refused["fix"], (argv, refused)
    assert not outside.exists() and not (project / ".git").exists()


def test_the_old_publish_and_select_check_the_same_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.orchestrate_test_case_revision import main

    project, run_root, attempt_id = _run(tmp_path / "p")
    candidate = run_root / "tmp" / "candidate.json"
    candidate.parent.mkdir(exist_ok=True)
    candidate.write_text("{}", encoding="utf-8")
    base = ["publish", "--candidate", str(candidate), "--run-root", str(run_root), "--attempt-id", attempt_id]
    outside = tmp_path / "outside"
    assert main([*base, "--output-dir", str(outside)]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["code"] == printed["reason_code"] == "OUTPUT_DIR_OUTSIDE_PROJECT" and printed["fix"] and not outside.exists()
    assert main([*base, "--output-dir", str(project / "bundle"), "--out", str(run_root / "receipt.json")]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["code"] == printed["reason_code"] == "API_OUT_OUTSIDE_RUN"
    select = ["select", "--candidate", str(candidate), "--candidate-receipt", str(candidate), "--assembly-receipt", str(candidate),
              "--inventory-receipt", str(candidate), "--context-receipt", str(candidate), "--context-marker-output", str(candidate),
              "--generator-fragment", str(candidate), "--reviewer-session", str(candidate), "--run-root", str(run_root), "--attempt-id", attempt_id]
    assert main([*select, "--output-dir", str(outside)]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["code"] == printed["reason_code"] == "OUTPUT_DIR_OUTSIDE_PROJECT" and not outside.exists()


# ---- every entry point -------------------------------------------------------------------------------------------

FLAGS = {"--out", "--output-dir"}
ENTRY_POINTS = {"tools/api.py", "tools/orchestrate_test_case_revision.py"}


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
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "output_guard"}
        assert {"run_output_directory"} <= used, relative
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "_out_target(" in text or "out_file(" in text, relative


def test_the_removed_publish_parser_left_no_unchecked_output_dir() -> None:
    assert "--output-dir" not in (ROOT / "tools" / "publish_test_case_bundle.py").read_text(encoding="utf-8")
