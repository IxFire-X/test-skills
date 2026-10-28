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
    pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-generator", invocation_id="invoke-" + stage.replace(":", "-"),
                                      input_digests=digests)


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
        assert found["unresolved"] == [] and len(found["files"]) == 2, (stage, found)
        places[stage] = [Path(row["path"]).relative_to(run_root).parts[0] for row in found["files"]]
    # the document file itself answers before the carrier that holds the same document inside
    assert places["tc-to-autotest:r1"] == ["candidate-bundle", "effective-canonicals"], places
    assert places["autotest-reviewer:r1"] == ["model-stage-artifacts", "automation-review-boundaries"], places


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
