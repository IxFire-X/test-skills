"""FaT-5: the context marker in parts — requirements from the documents once, code observations per context batch,
a merge by code into the same context-marker:baseline the generator and the reviewer already read."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pilot_state import AUTHORIZATION, _baseline, _module_digest
from tools import pilot_state
from tools.pilot_state import append_event, create_run

ROOT = Path(__file__).resolve().parents[1]


def _requirements() -> list[dict]:
    from tests.test_automation_revision_budget import automated_document

    return automated_document()["source_requirements"]


def _marker(sources: list[str], warnings: list[str] | None = None, requirements: list[dict] | None = None) -> dict:
    return {"schema_version": "5.0.0", "stage": "context-marker",
            "artifacts": {"analytics_documentation": {"requirements": requirements if requirements is not None else _requirements()},
                          "source_code_and_diff": {"sources": sources}},
            "warnings": list(warnings or [])}


def _run(tmp_path: Path, files: int = 3) -> tuple[Path, str, dict, list[dict]]:
    from tools.project_inventory import read_inventory_receipt, select_context_batches

    for index in range(files):
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / f"part{index}.py").write_text(f"# part {index}\n" + "X = 1\n" * 12000, encoding="utf-8")
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    baseline = _baseline(root)
    attempt = pilot_state.create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, baseline)
    inventory = read_inventory_receipt(root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    ids = [item["opaque_id"] for item in inventory["files"]]
    batches = select_context_batches(inventory, tmp_path, ids, byte_budget=100 * 1024)
    receipts = [pilot_state.publish_context_selection(root, attempt["attempt_id"], batch["receipt"]) for batch in batches]
    return root, attempt["attempt_id"], baseline, receipts


def _publish_requirements(root: Path, attempt_id: str, baseline: dict) -> dict:
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    pilot_state.publish_model_request(root, attempt_id, "context-marker:requirements", model_id="model-context", invocation_id="context-req",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])
    return pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _marker([REQUIREMENTS_PLACEHOLDER], ["док — требование без ожидаемого результата"]))


def _publish_part(root: Path, attempt_id: str, baseline: dict, index: int, receipt: dict, requirements: dict, value: dict) -> dict:
    stage = f"context-marker:batch-{index:03d}"
    pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-context", invocation_id=f"context-part-{index}",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipt["digest"], requirements["content_digest"]])
    return pilot_state.publish_model_stage_artifact(root, attempt_id, stage, value)


def test_three_batches_make_requirements_three_parts_and_a_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import step_size
    from tools.context_marker_merge import merge_marker_artifacts
    from tools.context_marker_parts import merge_parts, plan

    root, attempt_id, baseline, receipts = _run(tmp_path)
    assert len(receipts) == 3
    steps = plan(root)
    assert steps["mode"] == "parts" and [part["stage"] for part in steps["parts"]] == [
        "context-marker:requirements", "context-marker:batch-001", "context-marker:batch-002", "context-marker:batch-003"]
    assert all({"batch", "requirements", "role"} <= set(part["bytes"]) for part in steps["parts"][1:])
    requirements = _publish_requirements(root, attempt_id, baseline)
    answers = []
    for index, receipt in enumerate(receipts, start=1):
        value = _marker([f"src/part{index - 1}.py — наблюдение части {index}"], [f"часть {index} — код для SREQ-x не найден", "док — требование без ожидаемого результата"])
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, value)
        answers.append(value)
    assert plan(root)["next"] == "merge"
    monkeypatch.setattr(step_size, "DEFAULT_OUTPUT_WARN", 10)
    merged = merge_parts(root)
    # FaT-O: the merge is code — its size is measured, but no STEP_OUTPUT_LARGE about an answer the model never gave
    assert merged["output_bytes"] == merged["byte_count"] > 10 and merged["warning"] is None
    artifact = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", merged["content_digest"])["artifact"]
    expected = merge_marker_artifacts(_marker(["шаг требований — код не анализировался"], ["док — требование без ожидаемого результата"]), answers)
    assert artifact == expected and len(artifact["artifacts"]["source_code_and_diff"]["sources"]) == 3
    assert artifact["warnings"].count("док — требование без ожидаемого результата") == 1
    request = next(event for event in pilot_state.derive_state(root)["events"]
                   if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_REQUESTED")
    receipt = pilot_state.read_model_request(root, attempt_id, "context-marker:baseline", request["artifact_digest"])
    assert receipt["model_id"] == "controller:context-marker-merge"
    # the generator takes the merged marker like a single one
    from tests.helpers import publish_fixture_generator_inputs

    plan_digest, header_digest = publish_fixture_generator_inputs(root, attempt_id, ["BATCH-fixture"], receipts[0]["digest"])  # FaT-DG
    pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="model-generator", invocation_id="invoke-generator",
                                      input_digests=[merged["content_digest"], receipts[0]["digest"], plan_digest, header_digest])


def test_a_part_that_changes_the_requirements_is_rejected(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    changed = _requirements()
    changed[0] = {**changed[0], "text": changed[0]["text"] + " (переписано частью)"}
    with pytest.raises(ValueError, match="CONTEXT_MARKER_REQUIREMENTS_CHANGED"):
        _publish_part(root, attempt_id, baseline, 1, receipts[0], requirements, _marker(["src/part0.py — x"], requirements=changed))


def test_merge_stops_while_a_part_is_missing(tmp_path: Path) -> None:
    from tools.context_marker_parts import merge_parts

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    _publish_part(root, attempt_id, baseline, 1, receipts[0], requirements, _marker(["src/part0.py — x"]))
    with pytest.raises(ValueError, match="CONTEXT_MARKER_PARTS_INCOMPLETE"):
        merge_parts(root)
    with pytest.raises(ValueError):  # an answer that fails the schema is not published
        _publish_part(root, attempt_id, baseline, 2, receipts[1], requirements, {"schema_version": "5.0.0", "stage": "context-marker"})


def test_a_part_needs_the_requirements_answer_first(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    with pytest.raises(ValueError):
        pilot_state.publish_model_request(root, attempt_id, "context-marker:batch-001", model_id="model-context", invocation_id="early",
                                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"], "sha256:" + "c" * 64])


def test_a_forged_merged_marker_is_rejected(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    parts = [_publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — x"]))
             for index, receipt in enumerate(receipts, start=1)]
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts),
              requirements["content_digest"], *dict.fromkeys(part["content_digest"] for part in parts)]
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="controller:context-marker-merge",
                                      invocation_id="forged", input_digests=inputs)
    with pytest.raises(ValueError, match="merge"):
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker(["подделка — не слияние"]))


def test_one_batch_keeps_the_single_marker(tmp_path: Path) -> None:
    from tools.context_marker_parts import plan

    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    assert len(receipts) == 1
    steps = plan(root)
    assert steps["mode"] == "single" and [part["stage"] for part in steps["parts"]] == ["context-marker:baseline"]
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="model-context", invocation_id="single",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker(["src/part0.py — x"]))


def test_plan_names_a_part_over_budget_because_of_its_requirements(tmp_path: Path) -> None:
    from tools.context_marker_parts import plan

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    steps = plan(root, budget=len(json.dumps(_marker(["x"]), ensure_ascii=False).encode()) + 1000)
    over = [part for part in steps["parts"][1:] if not part["fits"]]
    assert over and all("limits.context_batch_bytes" in part["hint"] for part in over)
    assert requirements["content_digest"] in json.dumps(steps)


def test_the_cli_plan_and_status_name_the_merge_as_code(tmp_path: Path) -> None:
    from tools.run_pipeline import _next_step

    root, attempt_id, baseline, receipts = _run(tmp_path)
    completed = subprocess.run([sys.executable, "-m", "tools.context_marker_parts", "plan", "--run-root", str(root)],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["next"] == "context-marker:requirements" and "слияние частей кодом" in report["merge"]
    assert "слияние частей кодом" in _next_step({"next_expected_artifact": "context-marker parts merge"})["action"]


def test_a_merged_marker_passes_the_review_and_compatibility_checks(pack_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same durable run as test_compatibility_contract, with the marker made in parts: the checks are unchanged."""
    from tests.test_compatibility_contract import _durable_evidence
    from tests.test_execution_receipt import _execution_facts
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
    from tools.context_marker_parts import merge_parts
    from tools.release_manifest import build_release_manifest

    real_request, real_artifact = pilot_state.publish_model_request, pilot_state.publish_model_stage_artifact
    pending: dict = {}

    def request(run_root, attempt_id, stage, **options):
        if stage != "context-marker:baseline" or options.get("model_id", "").startswith("controller:"):
            return real_request(run_root, attempt_id, stage, **options)
        pending.update(options)
        return {}

    def artifact(run_root, attempt_id, stage, value):
        if stage != "context-marker:baseline" or not pending:
            return real_artifact(run_root, attempt_id, stage, value)
        inputs = pending.pop("input_digests")
        pending.clear()
        real_request(run_root, attempt_id, "context-marker:requirements", model_id="model-context", invocation_id=f"req-{attempt_id}", input_digests=inputs[:2])
        requirements = real_artifact(run_root, attempt_id, "context-marker:requirements",
                                     {**value, "artifacts": {**value["artifacts"], "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}}, "warnings": []})
        for index, receipt in enumerate(inputs[2:], start=1):
            stage_id = f"context-marker:batch-{index:03d}"
            real_request(run_root, attempt_id, stage_id, model_id="model-context", invocation_id=f"part-{index}-{attempt_id}",
                         input_digests=[*inputs[:2], receipt, requirements["content_digest"]])
            real_artifact(run_root, attempt_id, stage_id, value)
        return merge_parts(Path(run_root))

    monkeypatch.setattr(pilot_state, "publish_model_request", request)
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", artifact)
    run_root, attempt_id, _report, _request, _durable = _execution_facts(tmp_path)
    monkeypatch.undo()
    manifest = build_release_manifest(pack_root)
    evidence = _durable_evidence(manifest, run_root, attempt_id)
    event = next(event for event in pilot_state.derive_state(run_root)["events"]
                 if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_REQUESTED")
    merge_request = pilot_state.read_model_request(run_root, attempt_id, "context-marker:baseline", event["artifact_digest"])
    row = next(row for row in evidence["stages"] if row["stage"] == "context-marker")
    row.update(model_id=merge_request["model_id"], invocation_id=merge_request["invocation_id"], input_digests=merge_request["input_digests"])
    from tests.test_compatibility_contract import _digest

    evidence.pop("digest")
    evidence["digest"] = _digest(evidence)
    assert validate_compatibility_evidence(evidence, manifest, pack_root=pack_root, run_root=run_root) == {
        "level": "compatible", "independence": "verified", "errors": [],
    }


def test_a_merge_over_part_of_the_receipts_is_rejected(tmp_path: Path) -> None:
    from tools.context_marker_parts import merge_parts

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    parts = [_publish_part(root, attempt_id, baseline, index, receipts[index - 1], requirements, _marker([f"src/part{index - 1}.py — x"]))
             for index in (1, 2)]
    with pytest.raises(ValueError, match="CONTEXT_MARKER_PARTS_INCOMPLETE"):
        merge_parts(root)
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"], receipts[1]["digest"],
              requirements["content_digest"], *dict.fromkeys(part["content_digest"] for part in parts)]
    with pytest.raises(ValueError, match="CONTEXT_MARKER_PARTS_INCOMPLETE"):
        pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="controller:context-marker-merge",
                                          invocation_id="partial", input_digests=inputs)


def test_a_part_must_bind_its_own_receipt(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    with pytest.raises(ValueError, match="batch-002 must bind context receipt 2"):
        _publish_part(root, attempt_id, baseline, 2, receipts[0], requirements, _marker(["src/part0.py — x"]))
    _publish_part(root, attempt_id, baseline, 1, receipts[0], requirements, _marker(["src/part0.py — x"]))
    with pytest.raises(ValueError, match="must bind context receipt"):  # the same receipt again, as part 3
        _publish_part(root, attempt_id, baseline, 3, receipts[0], requirements, _marker(["src/part0.py — x"]))


def test_a_single_marker_must_bind_every_receipt(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    with pytest.raises(ValueError, match="every context receipt"):
        pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="model-context", invocation_id="single-partial",
                                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])


def test_a_part_plan_names_no_digests_until_the_requirements_answer(tmp_path: Path) -> None:
    """FaT-M (1.10): before the requirements answer a part has no input_digests with null in them — a note instead."""
    from tools.context_marker_parts import plan

    root, attempt_id, baseline, receipts = _run(tmp_path)
    parts = plan(root)["parts"][1:]
    assert all("input_digests" not in part and part["waits"] == "ждёт ответа context-marker:requirements" for part in parts)
    requirements = _publish_requirements(root, attempt_id, baseline)
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    parts = plan(root)["parts"][1:]
    assert [part["input_digests"] for part in parts] == [[*head, receipt["digest"], requirements["content_digest"]] for receipt in receipts]
    assert all("waits" not in part for part in parts)
