"""FaT-Q2 (2.2, 2.3): the input files of a generator or automation step from its published model request, and the
size of the context-marker:requirements step. Only a measurement: nothing is chosen, guessed or decided."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import pilot_state


def _generator_run(tmp_path: Path):
    from tests.test_marker_parts import _marker, _run
    from tools.batch_assembly import with_digest

    tmp_path.mkdir(parents=True, exist_ok=True)
    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    marker = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker(["src/part0.py — x"]))
    plan = with_digest({"kind": "plan", "batches": ["BATCH-fixture"]})
    header = with_digest({"kind": "header", "title": "Заголовок"})
    plan_path = pilot_state.publish_run_artifact_bytes(root, attempt_id, "batch-plan.json", json.dumps(plan, ensure_ascii=False).encode("utf-8"))["path"]
    header_path = pilot_state.publish_run_artifact_bytes(root, attempt_id, "generator-header.json", json.dumps(header, ensure_ascii=False).encode("utf-8"))["path"]
    return root, attempt_id, baseline, receipts, marker, plan, header, plan_path, header_path


def _request(root: Path, attempt_id: str, stage: str, digests: list[str]) -> None:
    # step_size measures requests of runs from before FaT-DG too: plan/header as plain run files, inputs unchecked
    with pytest.MonkeyPatch.context() as old:
        old.setattr(pilot_state, "_expected_model_inputs", lambda *_args: None)
        pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-generator", invocation_id="invoke-" + stage.replace(":", "-"),
                                          input_digests=digests)


def test_published_generator_inputs_are_found(tmp_path: Path) -> None:
    """FaT-DG: plan and header from publish-generator-input are found by their one digest."""
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, *_rest = _generator_run(tmp_path)
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Опубликованный заголовок"})
    plan = pilot_state.publish_generator_input(root, attempt_id, "plan", {"kind": "plan", "header_digest": header["digest"], "batches": [
        {"batch_id": "BATCH-fixture", "context_receipt_digest": receipts[0]["digest"], "header_digest": header["digest"]}]})
    pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="model-generator", invocation_id="invoke-generator",
                                      input_digests=[marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert found["unresolved"] == [], found
    assert {row["path"] for row in found["files"] if row["digest"] in {plan["digest"], header["digest"]}} == {str(root / plan["path"]), str(root / header["path"])}


def test_the_generator_inputs_come_from_the_published_request(tmp_path: Path) -> None:
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, plan, header, plan_path, header_path = _generator_run(tmp_path)
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert found["stage"] == "tc-generator:BATCH-fixture" and found["unresolved"] == [], found
    paths = [row["path"] for row in found["files"]]
    project = root.parent.parent
    assert str(root / "model-stage-artifacts" / attempt_id / (marker["content_digest"].removeprefix("sha256:") + ".json")) in paths
    assert str(project / "src" / "part0.py") in paths  # the context receipt opens into the project files the model reads
    assert str(root / plan_path) in paths and str(root / header_path) in paths
    assert all(row["bytes"] == Path(row["path"]).stat().st_size for row in found["files"])


def test_plan_and_header_published_without_a_suffix_are_found(tmp_path: Path) -> None:
    """FaT-10d: the microservices run published `--name batch-plan` and `--name batch-header`; both were STEP_INPUT_UNRESOLVED."""
    from tools.batch_assembly import with_digest
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, _plan, _header, _plan_path, _header_path = _generator_run(tmp_path)
    plan = with_digest({"kind": "plan", "batches": ["BATCH-fixture"], "name": "без суффикса"})
    header = with_digest({"kind": "header", "title": "Заголовок без суффикса"})
    paths = [pilot_state.publish_run_artifact_bytes(root, attempt_id, name, json.dumps(value, ensure_ascii=False).encode("utf-8"))["path"]
             for name, value in (("batch-plan", plan), ("batch-header", header))]
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert found["unresolved"] == [], found
    assert [row["path"] for row in found["files"] if row["digest"] in {plan["digest"], header["digest"]}] == [str(root / path) for path in paths]


def test_the_automation_inputs_come_from_the_published_requests(tmp_path: Path) -> None:
    from tests.test_finish import _project
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    places = {}
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        found = request_files(run_root, stage)
        assert found["unresolved"] == [], (stage, found)
        places[stage] = [Path(row["path"]).relative_to(run_root).parts[0] for row in found["files"]]
    # the document file itself answers before the carrier that holds the same document inside
    assert places["tc-to-autotest:r1"] == ["candidate-bundle"], places
    assert places["autotest-reviewer:r1"] == ["model-stage-artifacts", "automation-review-boundaries"], places


def test_the_receipt_inside_the_effective_carrier_is_a_binding_not_a_second_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FaT-AV: the effective bundle receipt has no file of its own; its carrier holds the whole document again (Petclinic
    1 603 574 B = 801 053 + 802 521). The step reads the receipt's own bytes, never the carrier."""
    from tests.test_finish import _project
    from tools import step_size
    from tools.pilot_state import read_effective_canonical

    project, run_root, attempt_id, _values = _project(tmp_path)
    effective = read_effective_canonical(run_root, attempt_id)
    found = step_size.request_files(run_root, "tc-to-autotest:r1")
    receipt = json.dumps(effective["effective_bundle_receipt"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert [row["digest"] for row in found["files"]] == [effective["document_digest"]], found
    assert [{key: row[key] for key in ("digest", "key", "bytes")} for row in found["bindings"]] == [
        {"digest": effective["effective_bundle_receipt_digest"], "key": "effective_bundle_receipt", "bytes": len(receipt)}], found
    assert Path(found["bindings"][0]["carrier"]).relative_to(run_root).parts[0] == "effective-canonicals"
    code = step_size.main(["--project", str(project), "--run", run_root.name, "--stage", "tc-to-autotest:r1"])
    report = json.loads(capsys.readouterr().out)
    document = Path(found["files"][0]["path"]).stat().st_size
    assert code == 0 and report["input_bytes"] == document + len(receipt) and report["size_complete"] is True, report


def test_the_automation_step_reads_the_autotest_view_when_it_is_built(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FaT-AV: like the reviewer, the generator reads the view of the effective document; the request digests stay."""
    from tests.test_finish import _project
    from tools import review_view, step_size
    from tools.pilot_state import read_effective_canonical

    project, run_root, attempt_id, _values = _project(tmp_path)
    effective = read_effective_canonical(run_root, attempt_id)
    code = review_view.main(["--project", str(project), "--run", run_root.name, "--effective"])
    built = json.loads(capsys.readouterr().out)
    assert code == 0, built
    found = step_size.request_files(run_root, "tc-to-autotest:r1")
    assert found["files"] == [{"digest": effective["document_digest"], "path": built["path"], "bytes": built["bytes"], "basis": "вид"}], found


def test_no_request_yet_is_an_error(tmp_path: Path) -> None:
    from tools.step_size import request_files

    root, *_rest = _generator_run(tmp_path)
    with pytest.raises(ValueError, match="model-request"):
        request_files(root, "tc-generator:BATCH-fixture")


def test_an_unknown_digest_is_unresolved_and_a_warning_not_a_stop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import step_size

    root, attempt_id, _baseline, receipts, marker, plan, _header, _plan_path, _header_path = _generator_run(tmp_path)
    unknown = "sha256:" + "2" * 64
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], unknown])
    code = step_size.main(["--project", str(root.parent.parent), "--run", root.name, "--stage", "tc-generator:BATCH-fixture"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["unresolved"] == [{"digest": unknown, "reason": "not-found"}], report
    assert report["size_complete"] is False and report["input_warning"] == "STEP_INPUT_UNRESOLVED" and report["role"] == "tc-generator"


def test_two_different_files_with_one_digest_are_ambiguous_not_chosen(tmp_path: Path) -> None:
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, plan, header, _plan_path, _header_path = _generator_run(tmp_path)
    twin = dict(plan, kind="другой план")  # same self "digest" field, other bytes
    pilot_state.publish_run_artifact_bytes(root, attempt_id, "twin-plan.json", json.dumps(twin, ensure_ascii=False).encode("utf-8"))
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert found["unresolved"] == [{"digest": plan["digest"], "reason": "ambiguous"}], found
    assert not any(row["digest"] == plan["digest"] for row in found["files"])


def test_the_run_record_has_no_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import step_size

    root, attempt_id, _baseline, receipts, marker, plan, header, _plan_path, _header_path = _generator_run(tmp_path)
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    code = step_size.main(["--project", str(root.parent.parent), "--run", root.name, "--stage", "tc-generator:BATCH-fixture"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["recorded"] and any("part0.py" in row["path"] for row in report["input_files"]), report
    line = (root / "step-sizes.jsonl").read_text(encoding="utf-8")
    assert "part0.py" not in line and "\\" not in line and "/" not in line.replace("tc-generator:BATCH-fixture", ""), line
    assert json.loads(line)["step"] == "tc-generator:BATCH-fixture"


def test_the_requirements_step_measures_only_the_documents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_marker_parts import _run
    from tools import step_size
    from tools.project_inventory import read_execution_baseline

    root, attempt_id, baseline, _receipts = _run(tmp_path, files=3)
    docs = [tmp_path / row["path"] for row in baseline["requirements"].get("docs", [])]
    code = step_size.main(["--project", str(tmp_path), "--run", root.name, "--stage", "context-marker:requirements"])
    report = json.loads(capsys.readouterr().out)
    role = sum(path.stat().st_size for path in step_size.role_files("context-marker"))
    assert code == 0 and report["files"] == len(docs) and report["bytes"] == sum(path.stat().st_size for path in docs) + role, report
    assert json.loads((root / "step-sizes.jsonl").read_text(encoding="utf-8").splitlines()[-1])["step"] == "context-marker:requirements"


def test_the_api_command_matches_the_function(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import api
    from tools.step_size import request_files

    root, attempt_id, _baseline, receipts, marker, plan, header, _plan_path, _header_path = _generator_run(tmp_path)
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    code = api.main(["step-inputs", "--run-root", str(root), "--stage", "tc-generator:BATCH-fixture"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["result"] == json.loads(json.dumps(api.plain(request_files(root, "tc-generator:BATCH-fixture")), ensure_ascii=False))
    code = api.main(["step-inputs", "--run-root", str(root), "--stage", "tc-to-autotest:r1"])
    assert code == 2 and json.loads(capsys.readouterr().out)["status"] == "error"


def test_a_document_kept_only_in_its_carrier_is_read_once_from_it(tmp_path: Path) -> None:
    """FaT-AV: the microservices run kept the case document in tmp/ only; the carrier is then the one file of the step
    and the receipt it also holds adds nothing."""
    from tests.test_finish import _project
    from tools.pilot_state import read_effective_canonical
    from tools.step_size import request_files

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    effective = read_effective_canonical(run_root, attempt_id)
    for path in (run_root / "candidate-bundle").rglob("*.json"):
        path.unlink()
    found = request_files(run_root, "tc-to-autotest:r1")
    assert [Path(row["path"]).relative_to(run_root).parts[0] for row in found["files"]] == ["effective-canonicals"], found
    assert [(row["digest"], row["bytes"]) for row in found["bindings"]] == [(effective["effective_bundle_receipt_digest"], 0)], found
