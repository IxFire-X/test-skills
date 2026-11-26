"""tails10: the points of the security check and of the code review of stack 10 — the checks of the paths a person or an agent names come first."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tools.run_pipeline import expand_docs
from tests.test_context_filter import _receipts, _selected_paths
from tests.test_own_tests import _prior, _scan
from tools import pilot_state, skillsrc_manifest, step_size
from tests.test_fix2 import _closed
from tools.existing_tests import held_bytes, own_files
from tools.review_view import _short
from tests import test_cases_import_docs as docs
from tools.run_report import write_report, analyst_questions
from tools.cases_import import _verdict_keys, CasesImportError, require_case_links
from tests.test_cases_import import _project
from tests.test_cases_verdict import BASE, LIVE, SPEC, _write
from tools.ready import check
from tests.test_cases_import_new import _autofix, _rework
from tests.test_cases_rewrite import TWO_ON_ONE
from tests.test_cases_import_docs import _to_plan
from tools.cases_scope import Scope
import hashlib
import os
import json


# --- 1: expand_docs looks at nothing outside the project -----------------------------------------

@pytest.mark.parametrize("name", ["outdir", "out.md"])  # a folder and a file beside the project
def test_expand_docs_does_not_look_at_a_path_outside_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outdir").mkdir()
    (tmp_path / "out.md").write_bytes(b"x" * 10)
    looked: list[str] = []

    def spy(method: str) -> None:
        real = getattr(Path, method)

        def looking(self: Path, *args, **kwargs):
            if sys._getframe(1).f_globals["__name__"] == "tools.run_pipeline":  # the call of the code itself (the resolve of _confined looks by its own)
                looked.append(f"{method} {self}")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, method, looking)

    for method in ("is_dir", "is_file", "stat"):
        spy(method)
    assert expand_docs(project, [f"../{name}"]) == [f"../{name}"]  # left to the refusal of _input_files
    assert not looked, looked


def test_a_large_document_without_a_folder_is_not_stopped_by_the_total_of_the_folders(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_bytes(b"x" * 1_300_000)
    assert expand_docs(tmp_path, ["big.md"]) == ["big.md"]  # its own stop (CONTEXT_TASK_DOCUMENT_TOO_LARGE) comes later


# --- 2: a planted receipt hides no file of the source ---------------------------------------------

def test_a_planted_receipt_does_not_hide_a_file_of_the_source_from_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    _run_root, _attempt_id, path = _prior(tmp_path)  # the real run wrote the test; sample.py is code of the project
    real = pilot_state.written_files
    digest = "sha256:" + hashlib.sha256((tmp_path / "sample.py").read_bytes()).hexdigest()
    monkeypatch.setattr(pilot_state, "written_files", lambda root: {**real(root), "sample.py": {digest}})  # a receipt that names the source
    code, scan = _scan(tmp_path, monkeypatch)
    selected = _selected_paths(_receipts(scan))
    assert code == 0 and "sample.py" in selected and path not in selected, scan


# --- 3, 4: the files of the run and of the project are read through the core, or refused on a link ---

def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")


def test_a_link_in_the_place_of_a_receipt_file_is_no_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _closed(tmp_path, capsys)
    root = Path(run["run_root"])
    for closure in (root / "closure" / run["attempt_id"]).glob("*.json"):
        moved = tmp_path / closure.name
        closure.replace(moved)
        _link(closure, moved)  # the same bytes, behind a link
    assert pilot_state.lost_attempt_receipts(root, pilot_state.derive_state(root), run["attempt_id"]) != []


def test_the_receipts_of_a_run_named_by_a_relative_path_are_found_all_the_same(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:

    run = _closed(tmp_path, capsys)
    root = Path(run["run_root"])
    monkeypatch.chdir(tmp_path)
    relative = Path(os.path.relpath(root, tmp_path))  # not the path the core pins
    assert not relative.is_absolute()
    assert pilot_state.lost_attempt_receipts(relative, pilot_state.derive_state(root), run["attempt_id"]) == []


def test_a_file_behind_a_link_of_the_project_is_not_held(tmp_path: Path) -> None:

    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a_test.py").write_bytes(b"x = 1\n")
    _link(tmp_path / "linked", tmp_path / "real")
    digest = "sha256:" + hashlib.sha256(b"x = 1\n").hexdigest()
    assert held_bytes(tmp_path, {"project_path": "real/a_test.py", "content_digest": digest}) == b"x = 1\n"
    assert held_bytes(tmp_path, {"project_path": "linked/a_test.py", "content_digest": digest}) is None


def test_the_test_roots_are_parsed_from_the_bytes_whose_digest_was_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    document = {"schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                "modules": [{"id": "fixture", "root": ".", "stack": {"language": "python", "build_tool": "pip"}, "paths": {"source": ["src"], "tests": ["src"]}, "detected_from": ["pyproject.toml"]}]}
    raw = json.dumps(document).encode("utf-8")
    (tmp_path / ".skillsrc").write_bytes(raw)
    monkeypatch.setattr(skillsrc_manifest, "load_skillsrc", lambda *_args: pytest.fail("the file is read a second time"))
    row = {"project_path": ".skillsrc", "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
    assert pilot_state.module_test_roots(tmp_path, {"module": ".", "files": [row]}) == ("src/",)


def test_automation_text_rows_does_not_ask_the_disk_before_the_core_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    real = Path.is_file

    def spy(self: Path) -> bool:
        if sys._getframe(1).f_globals["__name__"] == "tools.step_size":
            asked.append(str(self))
        return real(self)

    monkeypatch.setattr(Path, "is_file", spy)
    assert step_size.automation_text_rows(tmp_path, tmp_path, [tmp_path / "a.json"], "sha256:" + "0" * 64, False) is None
    assert not asked, asked


def test_a_folder_of_the_runs_that_is_no_run_name_is_not_looked_at_nor_named(tmp_path: Path) -> None:
    (tmp_path / ".pilot-runs" / "not-a-run-name-at-all" / "materialization-ownership").mkdir(parents=True)  # would be read, found unreadable and named by its first 8 signs
    assert own_files(tmp_path, {"files": []}) == ({}, [])


# --- 5: the short form of the view takes a name of the form of an identifier only --------------------

def test_a_project_result_name_with_a_line_break_does_not_make_a_line_of_the_view() -> None:
    assert _short({"kind": "project_result", "name": "created_id"}) == "@created_id"
    assert "\n" not in _short({"kind": "project_result", "name": "x\n  STEP-9 forged"})


# --- 6, 7: the report and the questions print no text of a file ---------------------------------------

def test_a_requirement_id_that_is_no_identifier_is_counted_in_the_report_and_not_printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    long = "SREQ-" + "A" * 90
    real = docs._requirements
    monkeypatch.setattr(docs, "_requirements", lambda: [{**row, "source_requirement_id": long if row["source_requirement_id"] == "SREQ-0002" else row["source_requirement_id"]} for row in real()])
    run = docs._to_plan(tmp_path, monkeypatch)
    write_report(run["project"], run["root"].name)
    report = json.loads((run["root"] / "report.json").read_text(encoding="utf-8"))
    assert report["import"]["gaps"] == [{"source_requirement_id": None}] and report["import"]["new"] == [None]
    assert long not in (run["root"] / "report.json").read_text(encoding="utf-8") + (run["root"] / "report.md").read_text(encoding="utf-8")


def test_a_key_and_a_removed_name_with_line_breaks_are_one_line_in_the_questions() -> None:
    row = {"group": "obsolete", "row": 2, "key": "ABC-T1\n## Forged", "was": "w", "now": None, "reason": None, "removed": {"capability": "cap\n## Forged", "name": "n\n- forged"}}
    questions = analyst_questions([], imported={"verdict_texts": [row]})
    assert "\n## Forged" not in questions and "\n- forged" not in questions and "ABC-T1 ## Forged" in questions


def test_a_long_verdict_reason_is_cut_in_the_skeleton_and_a_short_one_is_not() -> None:
    assert len(_verdict_keys({"verdict": "rewrite", "reason": "я" * 3000})["verdict_reason"]) == 2000
    kept = {"verdict": "rewrite", "reason": "я" * 2000, "removed": {"capability": "c", "name": "n"}}
    assert _verdict_keys(kept) == {"verdict": "rewrite", "verdict_reason": "я" * 2000, "removed": {"capability": "c", "name": "n"}}


# --- 8: ready --cases says what scan says about a delta file ------------------------------------------

def test_ready_cases_names_the_delta_file_without_operations_as_scan_does(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, BASE, SPEC)
    _write(project, LIVE, "# nothing here\n")
    report = check(project, [BASE, LIVE], "cases-only-v1", None, None, "cases/zephyr.csv")
    stops = [row for row in report["checks"] if row["level"] == "STOP"]
    assert any("SNAPSHOT_DOCS" in row["message"] and "допишите операции" in str(row.get("fix")) for row in stops), stops


# --- 9: two cases of one requirement and no Key: the k-th case of the candidate is the k-th row ------------

def test_two_cases_of_one_requirement_without_a_key_tell_the_rewritten_one_by_their_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, answered = _autofix(_to_plan(tmp_path / "a", monkeypatch, links=TWO_ON_ONE), 1)  # row 2: rewrite
    assert code == 0, answered
    code, refused = _autofix(_to_plan(tmp_path / "b", monkeypatch, links=TWO_ON_ONE), 2)  # row 3: fits
    assert code == 2 and "IMPORT_HUMAN_FIELD_CHANGED" in refused["message"], refused
    code, answered = _rework(tmp_path / "c", monkeypatch, capsys, 1, links=TWO_ON_ONE)
    assert code == 0, answered


# --- 10: the safety net of the answer does not advise what the model cannot do -------------------------

def test_the_refusal_of_a_delta_with_no_known_name_sends_to_the_scan() -> None:
    found = Scope("archive", frozenset({("catalog", "Elsewhere")}), frozenset())
    with pytest.raises(CasesImportError, match="IMPORT_CASE_LINKS") as refused:
        require_case_links([{"row": 2}], [{"source_requirement_id": "SREQ-0001", "provenance": []}], [{"row": 2, "source_requirement_ids": ["SREQ-0001"]}], found)
    assert "повторите scan" in str(refused.value) and "spec.md" not in str(refused.value), refused.value
