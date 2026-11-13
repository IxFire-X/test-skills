"""FaT-U7 (audit U7, F5): the inputs of a model step come from the journal of the attempt — one answer for the list of reading of prepare,
``tools.step_size --run --stage`` and the run report; no walk of the run, no file of the agent's ``tmp/``."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools import pilot_state


def _published_generator(tmp_path: Path):
    from tests.test_step_inputs import _generator_run

    root, attempt_id, _baseline, receipts, marker, *_rest = _generator_run(tmp_path)
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Опубликованный заголовок"})
    plan = pilot_state.publish_generator_input(root, attempt_id, "plan", {"kind": "plan", "header_digest": header["digest"], "batches": [
        {"batch_id": "BATCH-fixture", "context_receipt_digest": receipts[0]["digest"], "header_digest": header["digest"]}]})
    pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="model-generator", invocation_id="invoke-generator",
                                      input_digests=[marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    return root, attempt_id, plan, header


def test_copies_in_tmp_change_neither_the_paths_nor_the_bytes(tmp_path: Path) -> None:
    """The agent keeps copies of the plan and the header in tmp/ (Petclinic: the report took tmp/plan.json, 282 B for 27 447 B)."""
    from tools.step_size import stage_inputs

    root, _attempt_id, plan, header = _published_generator(tmp_path)
    before = stage_inputs(root, "tc-generator:BATCH-fixture")
    (root / "tmp").mkdir(exist_ok=True)
    for row in before["files"]:
        if Path(row["path"]).is_relative_to(root):
            shutil.copy(row["path"], root / "tmp" / Path(row["path"]).name)
    (root / "tmp" / "plan.json").write_text(json.dumps({"digest": plan["digest"]}), encoding="utf-8")  # a short copy, the same digest
    (root / "tmp" / "header.json").write_text(json.dumps({"digest": header["digest"]}), encoding="utf-8")
    after = stage_inputs(root, "tc-generator:BATCH-fixture")
    assert after == before and before["unresolved"] == [], (before, after)
    assert not any("tmp" in Path(row["path"]).relative_to(root).parts for row in after["files"] if Path(row["path"]).is_relative_to(root))


def test_a_journal_file_that_is_gone_is_not_in_journal_and_nothing_is_guessed(tmp_path: Path) -> None:
    from tools.step_size import stage_inputs

    root, attempt_id, _plan, header = _published_generator(tmp_path)
    (root / "generator-headers" / f"{attempt_id}.json").unlink()
    found = stage_inputs(root, "tc-generator:BATCH-fixture")
    assert found["unresolved"] == [{"digest": header["digest"], "reason": "not-in-journal"}], found
    assert not any(row["digest"] == header["digest"] for row in found["files"])


def test_the_run_is_never_walked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F5: the old index read and hashed every JSON of the run on every call."""
    from tools.step_size import stage_inputs

    root, *_rest = _published_generator(tmp_path)
    walked: list[tuple[Path, str]] = []
    real_rglob, real_glob = Path.rglob, Path.glob
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: walked.append((self, "**/" + pattern)) or real_rglob(self, pattern))
    monkeypatch.setattr(Path, "glob", lambda self, pattern: walked.append((self, pattern)) or real_glob(self, pattern))
    stage_inputs(root, "tc-generator:BATCH-fixture")
    assert not [pattern for folder, pattern in walked if folder == root and (pattern.startswith(("*", "**/")) or "**" in pattern)], walked


def test_the_reviewer_reads_its_view_and_the_package_binding(tmp_path: Path) -> None:
    """The package of the reviewer was never found (Petclinic final: STEP_INPUT_UNRESOLVED in the report); the journal keeps
    it as the package_binding of the reviewer ledger."""
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.canonical_document import document_sha256
    from tools.review_view import render
    from tools.step_size import stage_inputs

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=run_root / "cases")
    candidate = document_sha256(document)
    before = stage_inputs(run_root, "tc-reviewer:canonical")
    package = pilot_state.read_reviewer_session_ledger(run_root, attempt_id)["package_binding"]
    compact = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert before["unresolved"] == [] and [row["digest"] for row in before["files"]] == [candidate], before
    assert [(row["digest"], row["key"], row["bytes"]) for row in before["bindings"]] == [(package["package_digest"], "package_binding", len(compact))]
    view = render(document)
    (run_root / "review-views").mkdir(exist_ok=True)
    (run_root / "review-views" / (candidate.removeprefix("sha256:") + ".md")).write_bytes(view)
    after = stage_inputs(run_root, "tc-reviewer:canonical")
    assert [(row["basis"], row["bytes"]) for row in after["files"]] == [("вид", len(view))], after


def test_step_inputs_the_step_size_and_the_report_give_one_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _project
    from tools import step_size
    from tools.run_report import build_report

    project, run_root, _attempt_id, _values = _project(tmp_path)
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        found = step_size.request_files(run_root, stage)
        assert step_size.main(["--project", str(project), "--run", run_root.name, "--stage", stage]) == 0
        measured = json.loads(capsys.readouterr().out)
        call = next(call for call in build_report(project, run_root.name)[0]["model_calls"] if call["stage"] == stage)
        assert measured["input_bytes"] == step_size.input_size(found) == call["input_bytes"], (stage, measured, call)
        assert found["unresolved"] == [] and call["unresolved_inputs"] == 0 and measured["size_complete"] is True


def test_the_correction_also_reads_the_first_automation_and_its_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """For r2 the answers of tc-to-autotest:r1 and of autotest-reviewer:r1 bind the correction (predecessor and review
    digests); they are no input digest of the request, and the step reads them all the same."""
    from tests.test_finish import _project
    from tools.step_size import stage_inputs

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    r1 = stage_inputs(run_root, "tc-to-autotest:r1")
    events = pilot_state.derive_state(run_root)["events"]
    answers = {stage: next(str(event["artifact_digest"]) for event in events if event.get("stage_instance_id") == stage and event["event_type"] == kind)
               for stage, kind in (("tc-to-autotest:r1", "CANDIDATE_PUBLISHED"), ("autotest-reviewer:r1", "MODEL_RESPONSE_RECEIVED"))}
    request = pilot_state.read_model_request(run_root, attempt_id, "tc-to-autotest:r1", r1["request_digest"])
    monkeypatch.setattr(pilot_state, "_require_next_automation_stage", lambda *args: None)  # the fixture review is not AUTO_FIX_APPLIED
    monkeypatch.setattr(pilot_state, "_expected_model_inputs", lambda *args: None)
    pilot_state.publish_model_request(run_root, attempt_id, "tc-to-autotest:r2", model_id="model-automation", invocation_id="automation-2",
                                      input_digests=list(request["input_digests"]))
    monkeypatch.undo()
    r2 = stage_inputs(run_root, "tc-to-autotest:r2")
    extra = [(row["digest"], row["basis"]) for row in r2["files"] if row.get("basis") in {"r1", "ревью r1"}]
    assert extra == [(answers["tc-to-autotest:r1"], "r1"), (answers["autotest-reviewer:r1"], "ревью r1")], r2
    assert r2["files"][:len(r1["files"])] == r1["files"] and r2["bindings"] == r1["bindings"] and r2["unresolved"] == []
    assert list(request["input_digests"]) == list(pilot_state.read_model_request(
        run_root, attempt_id, "tc-to-autotest:r2", r2["request_digest"])["input_digests"])  # the protocol is unchanged


def test_the_requirements_step_measures_the_documents_of_the_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The baseline keeps only the digest of the --docs projection (W7, IMP3): the documents come from the scan request of
    the run, and in an import with documents the CSV as well — the same for stage_inputs and journal_files."""
    from tests.test_cases_import_docs import _request_requirements, _scanned
    from tools.step_size import journal_files, stage_inputs

    project, root, attempt_id = _scanned(tmp_path, monkeypatch)
    _request_requirements(root, attempt_id)
    found = stage_inputs(root, "context-marker:requirements")
    paths = sorted(Path(row["path"]).relative_to(project).as_posix() for row in found["files"] if Path(row["path"]).is_relative_to(project))
    assert {"docs/req.md", "cases/zephyr.csv"} <= set(paths) and found["unresolved"] == [], found
    documents = [path for path in journal_files(project, root.name, "context-marker:requirements", None)]
    assert sorted(path.relative_to(project).as_posix() for path in documents) == ["cases/zephyr.csv", "docs/req.md"]


def test_a_bundle_path_outside_the_project_is_unresolved_never_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The json_path of the effective bundle receipt is the agent's data: a file outside the project is not read."""
    from tests.test_finish import _project
    from tools import step_size

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    effective = pilot_state.read_effective_canonical(run_root, attempt_id)
    outside = tmp_path / "elsewhere" / "document.json"
    outside.parent.mkdir()
    outside.write_text("{}", encoding="utf-8")
    real = step_size._Journal.__init__

    def named_outside(self, *args, **kwargs):
        real(self, *args, **kwargs)
        self.bundled[str(effective["document_digest"])] = outside

    monkeypatch.setattr(step_size._Journal, "__init__", named_outside)
    found = step_size.stage_inputs(run_root, "tc-to-autotest:r1")
    assert {"digest": effective["document_digest"], "reason": "outside-project"} in found["unresolved"], found
    assert not any(row["path"] == str(outside) for row in found["files"])
