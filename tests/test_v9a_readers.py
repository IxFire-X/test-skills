"""V9a (arch review 3: A1-6, A1-8, A1-10): the readers that took a file of ``<run>`` for a fact now ask the journal.  Each test plants the file an agent
could put there and wants the number, the map or the decision to come from the journal."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.test_step_inputs import _generator_run, _request
from tools import pilot_state

STAGE = "tc-generator:BATCH-fixture"


def _published_request(tmp_path: Path):
    root, attempt_id, baseline, receipts, marker, plan, header, *_rest = _generator_run(tmp_path)
    _request(root, attempt_id, STAGE, [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    event = next(row for row in pilot_state.derive_state(root)["events"] if row["event_type"] == "MODEL_REQUESTED" and row.get("stage_instance_id") == STAGE)
    return root, attempt_id, baseline, receipts, marker, str(event["artifact_digest"])


# --- A1-6: step_size --------------------------------------------------------------------------------------------------

def test_a_request_file_with_other_input_digests_but_the_right_digest_field_is_not_measured(tmp_path: Path) -> None:
    from tools.step_size import request_files

    root, attempt_id, _baseline, _receipts, marker, request_digest = _published_request(tmp_path)
    assert request_files(root, STAGE)["files"]
    target = root / "model-requests" / attempt_id / (request_digest.removeprefix("sha256:") + ".json")
    planted = json.loads(target.read_text(encoding="utf-8"))
    planted["input_digests"] = [marker["content_digest"]]  # the digest field stays: only the bytes differ from what the journal sealed
    target.write_text(json.dumps(planted, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError):
        request_files(root, STAGE)


def test_a_context_selection_file_without_its_event_is_not_an_input_of_the_marker(tmp_path: Path) -> None:
    from tools.step_size import journal_files

    root, attempt_id, _baseline, receipts, *_rest = _published_request(tmp_path)
    project = root.parent.parent
    planted = dict(receipts[0], files=[{"project_path": "src/planted.py", "opaque_id": "x", "size": 1, "content_digest": "sha256:" + "0" * 64}])
    (root / "context-selections" / attempt_id / ("f" * 64 + ".json")).write_text(json.dumps(planted), encoding="utf-8")
    paths = journal_files(project, root.name, "context-marker", None)
    assert project / "src" / "planted.py" not in paths and project / "src" / "part0.py" in paths, paths


def test_the_limits_come_from_the_skillsrc_the_baseline_froze_and_from_nowhere_else(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.step_size import DEFAULT_BUDGET, LIMITS_DRIFT_NOTE, run_limits, step_budget

    root, attempt_id, baseline, *_rest = _published_request(tmp_path)
    project = root.parent.parent
    state = pilot_state.derive_state(root)
    limits, note = run_limits(project, root, state, attempt_id)
    assert note is None and step_budget(project, limits) == DEFAULT_BUDGET  # the file is the one the scan froze
    skillsrc = project / ".skillsrc"
    changed = json.loads(skillsrc.read_text(encoding="utf-8"))
    changed["limits"] = {"step_input_bytes": 1000}
    skillsrc.write_text(json.dumps(changed), encoding="utf-8")  # edited after the scan
    limits, note = run_limits(project, root, state, attempt_id)
    assert note == LIMITS_DRIFT_NOTE and step_budget(project, limits) == DEFAULT_BUDGET, (limits, note)
    # the same file, frozen: the values are the file's
    real = pilot_state.frozen_baseline
    monkeypatch.setattr(pilot_state, "frozen_baseline", lambda r, a: {**real(r, a), "skillsrc_authority": {
        "skillsrc_digest": "sha256:" + hashlib.sha256(skillsrc.read_bytes()).hexdigest()}})
    limits, note = run_limits(project, root, state, attempt_id)
    assert note is None and step_budget(project, limits) == 1000


def test_the_over_budget_warning_of_the_report_follows_the_calls_of_the_journal_not_the_rows_of_the_measurement_file() -> None:
    from tools.run_report import budget_warnings

    assert budget_warnings([], [{"fits": False}]) == []
    assert budget_warnings([{"by": "model", "fits": False}], []) == ["STEP_OVER_BUDGET"]


def test_the_step_size_rows_are_printed_as_the_agents_measurement() -> None:

    report = {"step_sizes": [{"step": "x", "bytes": 1, "input_bytes": 1, "role_bytes": 0, "role": None, "files": 1, "tokens_est": 1, "budget_bytes": 2,
                              "fits": True, "warning": None, "at": "t"}]}
    text = render_markdown_section(report)
    assert "замер агента" in text.lower() and "журналом не подтвержд" in text, text


def render_markdown_section(report: dict) -> str:
    from tools import run_report

    lines = [line for line in run_report._step_size_lines(report)]
    return "\n".join(lines)


def test_the_output_large_warning_of_the_report_is_judged_by_the_stored_answer_and_the_raw_size_is_printed_as_the_agents(tmp_path: Path, capsys, monkeypatch) -> None:
    from tests.helpers import answer_stage
    from tests.test_f1 import _batch_request, _report
    from tests.test_review_requested import _generated_run
    from tools import step_size

    monkeypatch.setattr(step_size, "DEFAULT_OUTPUT_WARN", 10_000_000)
    run = _generated_run(tmp_path, generated=False)
    stage, fragment = _batch_request(run, 0)
    code, answered = answer_stage(run["run_root"], stage, fragment)
    assert code == 0 and answered["result"]["warning"] is None, answered
    sizes = Path(run["run_root"]) / "output-sizes.jsonl"
    rows = [json.loads(line) for line in sizes.read_text(encoding="utf-8").splitlines()]
    sizes.write_text("".join(json.dumps({**row, "raw_bytes": 99_999_999}, sort_keys=True) + "\n" for row in rows), encoding="utf-8")  # an inflated row
    report = _report(run)
    call = next(row for row in report["model_calls"] if row["stage"] == stage)
    assert call["output_large"] is False and "STEP_OUTPUT_LARGE" not in report["warnings"], (report["warnings"], call)
    assert call["output_raw_bytes"] == 99_999_999 and call["output_basis"] == "compact", call


# --- A1-8: existing_tests -----------------------------------------------------------------------------------------------

def test_an_ownership_receipt_nobody_bound_excludes_no_test_from_the_map(tmp_path: Path) -> None:
    from tools.existing_tests import _excluded

    root, *_rest = _published_request(tmp_path)
    project = root.parent.parent
    planted = project / ".pilot-runs" / ("a" * 32) / "materialization-ownership" / ("b" * 32 + ".json")
    planted.parent.mkdir(parents=True)
    planted.write_text(json.dumps({"payload": {"files": [{"path": "src/part0.py"}]}}), encoding="utf-8")
    assert _excluded(project) == set()


def test_an_ownership_receipt_of_a_run_is_read_through_the_journal_of_that_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import existing_tests

    root, attempt_id, *_rest = _published_request(tmp_path)
    project = root.parent.parent
    asked = []

    def read(run_root, attempt, kind, event):
        asked.append((Path(run_root).name, attempt, kind, event))
        return {"record": {"payload": {"files": [{"path": "tests/test_generated.py"}]}}}

    monkeypatch.setattr(pilot_state, "read_attempt_receipt", read)
    (root / "materialization-ownership").mkdir(exist_ok=True)
    assert existing_tests._excluded(project) == {"tests/test_generated.py"}
    assert asked and asked[0][2:] == ("materialization-ownership", "ARTIFACT_READ_BACK") and asked[0][0] == root.name


def test_an_inventory_of_a_neighbour_run_under_the_expected_name_is_not_the_one_the_map_is_built_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import existing_tests
    from tools.project_inventory import _canonical_bytes, _digest

    root, _attempt_id, baseline, receipts, *_rest = _published_request(tmp_path)
    project = root.parent.parent
    name = str(receipts[0]["inventory_digest"]).removeprefix("sha256:") + ".json"
    real = json.loads((root / "inventories" / name).read_text(encoding="utf-8"))
    other = {key: value for key, value in real.items() if key != "digest"}
    other["files"] = other["files"][:1]
    other["digest"] = _digest(other)  # whole and sealed, but not the inventory of the expected digest
    (project / ".pilot-runs" / ("0" * 32) / "inventories").mkdir(parents=True)
    (project / ".pilot-runs" / ("0" * 32) / "inventories" / name).write_bytes(_canonical_bytes(other))
    seen = []
    monkeypatch.setattr(existing_tests, "build_map", lambda p, inventory, paths: seen.append(inventory["digest"]) or {})
    existing_tests.for_context(project, [receipts[0]])
    assert seen == [real["digest"]], seen


# --- A1-10 ---------------------------------------------------------------------------------------------------------------

def test_a_bundle_csv_that_is_not_the_csv_of_the_document_is_refused_by_the_zephyr_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_zephyr_template import _cases_only_run
    from tools.zephyr_template_export import ZephyrTemplateError, export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    bundle_csv.write_bytes(bundle_csv.read_bytes() + b"\xd0\x9f\xd0\xbe\xd0\xb4\xd0\xbb\xd0\xbe\xd0\xb6\xd0\xb5\xd0\xbd\xd0\xbd\xd0\xb0\xd1\x8f,\xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd0\xba\xd0\xb0\n")
    with pytest.raises(ZephyrTemplateError) as refused:
        export_import_csv(project, run_root.name, bundle_csv)
    assert refused.value.code == "ZEPHYR_EXPORT_CASES"


def test_the_report_prints_the_zephyr_import_only_for_the_template_it_was_made_from(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_zephyr_template import _cases_only_run
    from tools.run_report import _zephyr_import
    from tools.zephyr_template_export import export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    export_import_csv(project, run_root.name, bundle_csv)
    record = _zephyr_import(run_root)
    assert record["template_path"] == "zephyr/stubs.csv" and "журналом не подтверждено" in record["basis"], record
    (project / "zephyr" / "stubs.csv").write_bytes((project / "zephyr" / "stubs.csv").read_bytes() + b"x")  # the template is not the one the file says
    assert _zephyr_import(run_root) is None
    forged = json.loads((run_root / "zephyr-import.json").read_text(encoding="utf-8"))
    forged["template_digest"] = "sha256:" + hashlib.sha256((project / "zephyr" / "stubs.csv").read_bytes()).hexdigest()
    forged["rows"] = 99999
    (run_root / "zephyr-import.json").write_text(json.dumps(forged), encoding="utf-8")
    assert _zephyr_import(run_root)["rows"] == 99999  # labelled as the file's, not as a fact


def test_an_inventory_without_its_event_does_not_make_a_moved_project_work(tmp_path: Path) -> None:
    from tools.project_inventory import _canonical_bytes, _digest

    root, _attempt_id, _baseline, receipts, *_rest = _published_request(tmp_path / "work")
    source = root.parent.parent
    target = tmp_path / "copy" / "project"
    target.parent.mkdir()
    shutil.copytree(source, target)
    moved_root = target / ".pilot-runs" / root.name
    assert pilot_state.run_project_move(target, moved_root)["same_structure"] is True
    name = str(receipts[0]["inventory_digest"]).removeprefix("sha256:") + ".json"
    real = json.loads((moved_root / "inventories" / name).read_text(encoding="utf-8"))
    (moved_root / "inventories" / name).unlink()  # the journal binds this inventory; the file is gone
    planted = {key: value for key, value in real.items() if key != "digest"}
    planted["files"] = []
    planted["digest"] = _digest(planted)
    (moved_root / "inventories" / (planted["digest"].removeprefix("sha256:") + ".json")).write_bytes(_canonical_bytes(planted))
    assert pilot_state.run_project_move(target, moved_root)["same_structure"] is False


def test_the_manifest_of_the_run_is_read_through_read_run_by_cases_import_and_step_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import cases_import, step_size

    root, *_rest = _published_request(tmp_path)
    asked = []
    real = pilot_state.read_run
    monkeypatch.setattr(pilot_state, "read_run", lambda r: asked.append(("read_run", Path(r).name)) or real(r))
    cases_import.cases_path(root)
    cases_import.docs_run(root)
    step_size._requirement_files(root.parent.parent, root)
    assert len(asked) >= 3, asked
