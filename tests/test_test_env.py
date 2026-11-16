"""outfix1-b: the automation stages read the build files of the module and the imports of the project's own tests; every stage that writes a capability reads the adapter the run froze."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_existing_tests import _write
from tests.test_finish import _project

AUTOTEST_STAGES = ("tc-to-autotest:r1", "autotest-reviewer:r1")


@pytest.mark.parametrize("stage", AUTOTEST_STAGES)
def test_an_automation_stage_reads_the_adapter_the_run_froze(tmp_path: Path, stage: str) -> None:
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    assert request_files(run_root, stage)["adapter"]["id"] == "pytest:selected-symbols-v1"


@pytest.mark.parametrize("stage", AUTOTEST_STAGES)
def test_an_automation_stage_gets_the_adapter_as_a_reference_not_a_rule_it_cannot_follow(tmp_path: Path, stage: str) -> None:
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    assert "capability" not in request_files(run_root, stage)["adapter"]["rule"]  # the stage that writes ``capability.adapter`` is the generator of the cases


def test_a_batch_of_the_case_generator_reads_the_adapter_the_run_froze(tmp_path: Path) -> None:
    from tools.step_size import with_test_env

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    found = with_test_env(run_root, {"stage": "tc-generator:BATCH-fixture", "attempt_id": attempt_id, "files": []})
    assert found["adapter"]["id"] == "pytest:selected-symbols-v1" and "capability.adapter" in found["adapter"]["rule"] and found["files"] == []


def test_a_batch_of_a_cases_only_run_reads_no_adapter_and_is_not_refused(tmp_path: Path) -> None:
    from tests.test_step_inputs import _generator_run, _request
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, plan, header, *_rest = _generator_run(tmp_path)
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    assert "adapter" not in request_files(root, "tc-generator:BATCH-fixture")


def test_a_baseline_receipt_the_code_cannot_read_is_a_refusal_for_a_batch_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.step_size import with_test_env

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    monkeypatch.setattr(pilot_state, "frozen_baseline", lambda *_args: (_ for _ in ()).throw(ValueError("BASELINE_UNBOUND")))
    with pytest.raises(ValueError, match="BASELINE_UNBOUND"):
        with_test_env(run_root, {"stage": "tc-generator:BATCH-fixture", "attempt_id": attempt_id, "files": []})


def test_a_receipt_of_the_inventory_the_code_cannot_read_is_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.step_size import stage_inputs, with_test_env

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    found = stage_inputs(run_root, AUTOTEST_STAGES[0])
    monkeypatch.setattr(pilot_state, "frozen_inventory", lambda *_args: (_ for _ in ()).throw(ValueError("INVENTORY_UNBOUND")))
    with pytest.raises(ValueError, match="INVENTORY_UNBOUND"):
        with_test_env(run_root, found)


def test_the_reviewer_of_the_cases_reads_no_adapter(tmp_path: Path) -> None:
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    assert "adapter" not in request_files(run_root, "tc-reviewer:canonical")


@pytest.mark.parametrize("stage", AUTOTEST_STAGES)
def test_an_automation_stage_reads_the_imports_of_the_project_tests(tmp_path: Path, stage: str) -> None:
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    [row] = [row for row in request_files(run_root, stage)["files"] if row.get("basis") == "окружение тестов"]
    text = Path(row["path"]).read_text(encoding="utf-8")
    assert "адаптер: pytest:selected-symbols-v1" in text and "import pytest" in text and row["bytes"] == len(text.encode("utf-8"))


def test_the_request_digest_does_not_change(tmp_path: Path) -> None:
    from tools.step_size import request_files, stage_inputs

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    assert request_files(run_root, AUTOTEST_STAGES[0])["request_digest"] == stage_inputs(run_root, AUTOTEST_STAGES[0])["request_digest"]


def test_the_report_and_the_step_count_the_same_files(tmp_path: Path) -> None:
    from tools.run_report import build_report
    from tools.step_size import input_size, request_files

    project, run_root, _attempt_id, _values = _project(tmp_path)
    call = next(call for call in build_report(project, run_root.name)[0]["model_calls"] if call["stage"] == AUTOTEST_STAGES[0])
    assert call["input_bytes"] == input_size(request_files(run_root, AUTOTEST_STAGES[0]))


def test_nothing_is_written_when_the_views_are_not(tmp_path: Path) -> None:
    from tools.step_size import stage_inputs, with_test_env

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    for old in (run_root / "review-views").glob("*.test-env.md"):  # the fixture has run ``prepare`` already
        old.unlink()
    found = with_test_env(run_root, stage_inputs(run_root, AUTOTEST_STAGES[0]), write=False)
    assert [row for row in found["files"] if row.get("basis") == "окружение тестов"] and not list((run_root / "review-views").glob("*.test-env.md"))


def test_the_stage_of_the_cases_gets_no_test_environment(tmp_path: Path) -> None:
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    assert not [row for row in request_files(run_root, "tc-reviewer:canonical")["files"] if row.get("basis") in {"окружение тестов", "файл сборки"}]


# --- the imports ---------------------------------------------------------------------------------

def _view(tmp_path: Path, rows: list[dict], limit: int = 8192) -> str:
    from tools.existing_tests import env_view

    return env_view(tmp_path, {"files": rows}, ["# Окружение тестов"], limit).decode("utf-8")


def test_an_import_is_counted_by_the_files_that_hold_it(tmp_path: Path) -> None:
    rows = [_write(tmp_path, "src/test/java/ATest.java", "import org.junit.jupiter.api.Test;\nimport java.util.List;\nclass ATest {}\n"),
            _write(tmp_path, "src/test/java/BTest.java", "import org.junit.jupiter.api.Test;\nclass BTest {}\n")]
    lines = _view(tmp_path, rows).splitlines()
    assert lines[1:] == ["2 import org.junit.jupiter.api.Test;", "1 import java.util.List;"]


def test_a_python_import_is_taken_from_the_top_level_only(tmp_path: Path) -> None:
    rows = [_write(tmp_path, "tests/test_a.py", "import os\nfrom pathlib import Path\n\n\ndef test_a():\n    import json\n")]
    assert _view(tmp_path, rows).splitlines()[1:] == ["1 from pathlib import Path", "1 import os"]


def test_a_test_the_package_wrote_is_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import existing_tests

    rows = [_write(tmp_path, "tests/test_own.py", "import own_thing\n"), _write(tmp_path, "tests/test_human.py", "import human_thing\n")]
    monkeypatch.setattr(existing_tests, "own_files", lambda _project, _inventory: ({"tests/test_own.py": "abc"}, []))
    assert _view(tmp_path, rows).splitlines()[1:] == ["1 import human_thing"]


def test_a_test_changed_after_the_scan_is_left_out(tmp_path: Path) -> None:
    rows = [_write(tmp_path, "tests/test_a.py", "import first\n")]
    (tmp_path / "tests" / "test_a.py").write_text("import second\n", encoding="utf-8")
    assert _view(tmp_path, rows) == ""


def test_a_file_that_is_no_test_gives_no_import(tmp_path: Path) -> None:
    rows = [_write(tmp_path, "src/main/java/App.java", "import java.util.List;\nclass App {}\n")]
    assert _view(tmp_path, rows) == ""


def test_the_imports_are_cut_by_bytes_and_the_rest_is_counted(tmp_path: Path) -> None:
    rows = [_write(tmp_path, "tests/test_a.py", "".join(f"import module_{index:03d}\n" for index in range(100)))]
    text = _view(tmp_path, rows, limit=600)
    assert len(text.encode("utf-8")) <= 600 and text.splitlines()[-1].startswith("… ещё ") and text.endswith(" строк\n")


# --- the build files -----------------------------------------------------------------------------

def test_the_build_files_are_the_module_and_the_reactor_parents_the_inventory_holds(tmp_path: Path) -> None:
    from tools.step_size import build_file_rows

    parent = '<project><modules><module>api</module></modules></project>'
    child = '<project><parent><artifactId>root</artifactId></parent></project>'
    rows = [_write(tmp_path, "pom.xml", parent), _write(tmp_path, "api/pom.xml", child)]
    found = build_file_rows(tmp_path, {"files": rows}, tmp_path / "api")
    assert [(row["path"], row["basis"]) for row in found] == [(str(tmp_path / "api" / "pom.xml"), "файл сборки"), (str(tmp_path / "pom.xml"), "файл сборки")]
    assert found[0]["bytes"] == len(child) and found[0]["digest"] == "sha256:" + hashlib.sha256(child.encode()).hexdigest()


def test_a_build_file_missing_from_the_inventory_is_not_read(tmp_path: Path) -> None:
    from tools.step_size import build_file_rows

    _write(tmp_path, "pom.xml", "<project/>")
    assert build_file_rows(tmp_path, {"files": []}, tmp_path) == []


def test_a_module_root_outside_the_project_gives_no_build_file(tmp_path: Path) -> None:
    from tools.step_size import build_file_rows

    project, outside = tmp_path / "project", tmp_path / "outside"
    rows = [_write(project, "pom.xml", "<project/>")]
    _write(outside, "pom.xml", "<project/>")
    assert build_file_rows(project, {"files": rows}, outside) == []


def test_a_build_file_changed_after_the_scan_is_not_read(tmp_path: Path) -> None:
    from tools.step_size import build_file_rows

    rows = [_write(tmp_path, "pom.xml", "<project/>")]
    (tmp_path / "pom.xml").write_text("<project><x/></project>", encoding="utf-8")
    assert build_file_rows(tmp_path, {"files": rows}, tmp_path) == []


def test_a_gradle_build_file_is_read_with_the_module(tmp_path: Path) -> None:
    from tools.step_size import build_file_rows

    rows = [_write(tmp_path, "build.gradle.kts", "plugins {}\n")]
    assert [row["path"] for row in build_file_rows(tmp_path, {"files": rows}, tmp_path)] == [str(tmp_path / "build.gradle.kts")]
