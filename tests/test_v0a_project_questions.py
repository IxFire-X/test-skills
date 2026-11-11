"""V0a (C7, C6): a question about the project is closed by its answer, a foreign manifest is no module, and the stop says how to answer."""
from __future__ import annotations

import json
from pathlib import Path

PYPROJECT = '[project]\nname = "{}"\nversion = "1"\ndependencies = ["pytest"]\n'


def _module(root: Path, name: str, *, sources: str | None = "src") -> Path:
    folder = root / name if name else root
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pyproject.toml").write_text(PYPROJECT.format(name or "main"), encoding="utf-8")
    if sources:
        (folder / sources).mkdir(exist_ok=True)
        (folder / sources / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return folder


def _questions(report: dict) -> dict:
    return {question["id"]: question for question in report.get("questions") or []}


def test_a_folder_with_a_foreign_manifest_and_no_sources_is_no_module(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    _module(tmp_path, "")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
    report = ensure_skillsrc(tmp_path, {}, False)
    assert report["status"] in {"preview", "created"} and not _questions(report), report
    assert any("examples" in warning for warning in report["warnings"]), report


def test_the_answer_to_a_source_question_is_a_path_and_closes_it(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    _module(tmp_path, "app")
    bare = _module(tmp_path, "scripts", sources=None)
    (bare / "run").mkdir()
    (bare / "run" / "go.py").write_text("print(1)\n", encoding="utf-8")
    first = ensure_skillsrc(tmp_path, {}, False)
    assert list(_questions(first)) == ["module:scripts:paths.source"], first
    done = ensure_skillsrc(tmp_path, {"module:scripts:paths.source": "run"}, True)
    assert done["status"] == "created", done
    modules = {module["id"]: module for module in normalize_skillsrc(load_skillsrc(tmp_path / ".skillsrc"))["modules"]}
    assert modules["scripts"]["paths"]["source"] == ["run"]
    assert ensure_skillsrc(tmp_path, {}, False)["status"] == "unchanged"


def test_the_literal_option_of_a_source_question_is_refused_with_the_way_out(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    _module(tmp_path, "app")
    _module(tmp_path, "scripts", sources=None)
    report = ensure_skillsrc(tmp_path, {"module:scripts:paths.source": "provide-source-root"}, True)
    assert report["status"] == "error" and report["errors"] == ["source_root_invalid"], report
    from tools.init_skillsrc import SOURCE_ROOT_FIX

    assert report["_fix"] == SOURCE_ROOT_FIX, report


def test_a_one_option_question_is_closed_by_its_answer(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    folder = _module(tmp_path, "")
    (folder / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\n', encoding="utf-8")
    first = ensure_skillsrc(tmp_path, {}, False)
    answers = {key: question["options"][0]["id"] for key, question in _questions(first).items()}
    assert answers == {"module:root:test.framework": "pytest"}, first
    assert ensure_skillsrc(tmp_path, answers, True)["status"] == "created"
    again = ensure_skillsrc(tmp_path, {}, False)
    assert again["status"] == "unchanged" and not _questions(again), again


def test_the_stop_of_ready_prints_the_format_of_the_answers_and_the_command(tmp_path: Path) -> None:
    from tools.ready import check_skillsrc

    _module(tmp_path, "app")
    _module(tmp_path, "scripts", sources=None)
    row = check_skillsrc(tmp_path)[0]
    fix = str(row["fix"])
    assert row["level"] == "STOP" and "--answers" in fix and "--write" in fix, row
    assert '"module:scripts:paths.source"' in fix, fix


def test_the_scan_does_not_call_an_answered_framework_a_default(tmp_path: Path) -> None:
    from tools.run_pipeline import _skillsrc_defaults

    folder = _module(tmp_path, "")
    (folder / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1"\n', encoding="utf-8")
    note = _skillsrc_defaults(tmp_path, {"id": "root", "root": ".", "stack": {"language": "python"}, "test": {"framework": "pytest"}})
    assert note and "по умолчанию" not in json.dumps(note, ensure_ascii=False)


def test_the_answers_file_in_the_suggested_place_is_accepted_and_is_no_drift_of_the_inventory(tmp_path: Path) -> None:
    import subprocess
    import sys

    from tools.init_skillsrc import answers_hint
    from tools.project_inventory import build_inventory

    _module(tmp_path, "app")
    bare = _module(tmp_path, "scripts", sources=None)
    (bare / "run").mkdir()
    (bare / "run" / "go.py").write_text("print(1)\n", encoding="utf-8")
    from tools.init_skillsrc import ensure_skillsrc

    hint = answers_hint(tmp_path, list(_questions(ensure_skillsrc(tmp_path, {}, False)).values()))
    assert ".pilot-runs/init/answers.json" in hint
    before = build_inventory(tmp_path, tmp_path / "app")["digest"]
    answers = tmp_path / ".pilot-runs" / "init" / "answers.json"
    answers.parent.mkdir(parents=True)
    answers.write_text(json.dumps({"module:scripts:paths.source": "run"}), encoding="utf-8")
    assert build_inventory(tmp_path, tmp_path / "app")["digest"] == before  # the answers file is no drift of the inventory
    ran = subprocess.run([sys.executable, "-m", "tools.init_skillsrc", "--project", str(tmp_path), "--answers", str(answers), "--write"],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8")
    assert ran.returncode == 0 and json.loads(ran.stdout)["status"] == "created", ran.stdout + ran.stderr


import pytest


@pytest.mark.parametrize("answer", ["../outside", "/abs", r"C:\abs", ".", "missing", "provide-source-root"])
def test_a_source_answer_that_leaves_the_module_or_is_no_folder_is_refused(tmp_path: Path, answer: str) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    _module(tmp_path, "app")
    _module(tmp_path, "scripts", sources=None)
    (tmp_path / "outside").mkdir()
    report = ensure_skillsrc(tmp_path, {"module:scripts:paths.source": answer}, True)
    assert report["status"] == "error" and report["errors"] == ["source_root_invalid"] and not (tmp_path / ".skillsrc").exists(), report


def test_a_linked_folder_is_not_taken_as_a_source_answer(tmp_path: Path) -> None:
    import os
    import subprocess

    from tools.init_skillsrc import ensure_skillsrc

    _module(tmp_path, "app")
    bare = _module(tmp_path, "scripts", sources=None)
    target = tmp_path / "app" / "src"
    link = bare / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    report = ensure_skillsrc(tmp_path, {"module:scripts:paths.source": "linked"}, True)
    assert report["status"] == "error" and report["errors"] == ["source_root_invalid"], report


def test_a_backslash_answer_is_written_with_a_slash(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    _module(tmp_path, "app")
    bare = _module(tmp_path, "scripts", sources=None)
    (bare / "run" / "deep").mkdir(parents=True)
    (bare / "run" / "deep" / "go.py").write_text("print(1)\n", encoding="utf-8")
    assert ensure_skillsrc(tmp_path, {"module:scripts:paths.source": r"run\deep"}, True)["status"] == "created"
    modules = {module["id"]: module for module in normalize_skillsrc(load_skillsrc(tmp_path / ".skillsrc"))["modules"]}
    assert modules["scripts"]["paths"]["source"] == ["run/deep"]


def test_the_needs_input_output_of_init_adds_only_the_fix_to_the_schema_of_the_receipt(tmp_path: Path) -> None:
    """``main()`` prints the receipt with a ``fix`` (it already prints ``message`` and ``fix`` of an error); no check runs the schema
    against that output, and everything but those two keys still fits it."""
    import subprocess
    import sys

    from jsonschema import Draft202012Validator

    _module(tmp_path, "app")
    _module(tmp_path, "scripts", sources=None)
    root = Path(__file__).resolve().parents[1]
    ran = subprocess.run([sys.executable, "-m", "tools.init_skillsrc", "--project", str(tmp_path)], cwd=root, capture_output=True, text=True, encoding="utf-8")
    output = json.loads(ran.stdout)
    assert ran.returncode == 3 and "--answers" in output["fix"], ran.stdout
    schema = json.loads((root / "schemas" / "skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate({key: value for key, value in output.items() if key not in {"fix", "message"}})
