"""F2 (A2-2, A2-4, arch review 3): cases-import, the merge of the marker parts (merge_parts) and the batch skeletons of a plan cut between two writes —
the same command again finishes the work."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cases_import import _project, _scan
from tests.test_review_open_recovery import _crash_once, _main
from tests.test_cut_review import _crash_event, _crash_nth, _events


def _import_argv(root: Path, attempt_id: str) -> list[str]:
    return ["cases-import", "--run-root", str(root), "--attempt", attempt_id]


def _stage_events(root: Path, stage: str) -> list[str]:
    return [row["event_type"] for row in _events(root) if row.get("stage_instance_id") == stage]


# --------------------------------------------------------------------------------------
# cases-import (no --docs): imported-cases receipt + pair, model request file + MODEL_REQUESTED, answer file + MODEL_RESPONSE_RECEIVED
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["skeleton-file-written", "skeleton-published", "request-written", "answer-file-written"])
def test_cases_import_cut_between_its_writes(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    from tools import pilot_state

    project = _project(tmp_path)
    _code, scan = _scan(project, monkeypatch)
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    with monkeypatch.context() as broken:
        if point == "skeleton-file-written":
            _crash_event(broken, "ARTIFACT_PUBLISHED", None)
        elif point == "skeleton-published":
            _crash_event(broken, "ARTIFACT_READ_BACK", None)
        elif point == "request-written":
            _crash_once(broken, pilot_state, "publish_model_stage_artifact")
        else:
            _crash_event(broken, "MODEL_RESPONSE_RECEIVED", None)
        code, first = _main(capsys, _import_argv(root, attempt_id))
        assert code == 2, first
    code, again = _main(capsys, _import_argv(root, attempt_id))
    assert code == 0, f"{point}: {again}"
    assert _stage_events(root, "context-marker:requirements") == ["MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED"]
    assert again["result"]["imported"]["cases"] == 2, again


# --------------------------------------------------------------------------------------
# the merge of the marker parts (merge_parts): request file + MODEL_REQUESTED, answer file + MODEL_RESPONSE_RECEIVED, questions draft
# --------------------------------------------------------------------------------------

def _parts_answered(tmp_path: Path):
    from tests.test_marker_parts import _marker, _publish_part, _publish_requirements, _run

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements,
                      _marker([f"src/part{index - 1}.py — наблюдение части {index}"], [f"часть {index} — код для SREQ-x не найден"]))
    return root, attempt_id


@pytest.mark.parametrize("point", ["request-written", "answer-file-written"])
def test_merge_cut_between_the_request_and_the_answer(tmp_path: Path, monkeypatch, point: str) -> None:
    from tools import pilot_state
    from tools.context_marker_parts import merge_parts

    root, attempt_id = _parts_answered(tmp_path)
    with monkeypatch.context() as broken:
        if point == "request-written":
            _crash_once(broken, pilot_state, "publish_model_stage_artifact")
        else:
            _crash_event(broken, "MODEL_RESPONSE_RECEIVED", None)
        with pytest.raises(OSError):
            merge_parts(root)
    assert _stage_events(root, "context-marker:baseline") == ["MODEL_REQUESTED"]
    merged = merge_parts(root)  # the same command again finishes the merge
    assert merged["content_digest"].startswith("sha256:"), merged
    assert _stage_events(root, "context-marker:baseline") == ["MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED"]


# --------------------------------------------------------------------------------------
# publish-generator-input --kind plan of a --cases run: the plan receipt + pair, then one skeleton receipt + pair per batch
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["skeleton-file-written", "skeleton-published"])
def test_plan_of_an_import_cut_between_a_skeleton_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    from tests.test_batch_assembly import _header
    from tests.test_review_requested import _put
    from tools import context_marker_parts, pilot_state
    from tools.batch_assembly import plan_batches
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    project = _project(tmp_path)
    _code, scan = _scan(project, monkeypatch)
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    assert _main(capsys, _import_argv(root, attempt_id))[0] == 0
    state = pilot_state.derive_state(root)
    answer_digest = next(row["artifact_digest"] for row in state["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED")
    answer = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer_digest)["artifact"]
    assert answer["artifacts"]["source_code_and_diff"]["sources"] == [REQUIREMENTS_PLACEHOLDER]
    [part] = [row for row in context_marker_parts.plan(root)["parts"] if row["stage"] == "context-marker:batch-001"]
    pilot_state.publish_model_request(root, attempt_id, "context-marker:batch-001", model_id="model-context", invocation_id="context-part-1",
                                      input_digests=part["input_digests"])
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:batch-001", {
        **answer, "artifacts": {**answer["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine склеивает два значения"]}}})
    context_marker_parts.merge_parts(root)
    context = next(pilot_state.read_context_selection(root, attempt_id, row["artifact_digest"]) for row in pilot_state.derive_state(root)["events"]
                   if row["event_type"] == "CONTEXT_SELECTED")
    header = pilot_state.publish_generator_input(root, attempt_id, "header", _header())
    plan = plan_batches(answer["artifacts"]["analytics_documentation"]["requirements"],
                        {"header_digest": header["digest"], "context_receipt_digest": context["digest"]})
    argv = ["publish-generator-input", "--run-root", str(root), "--attempt", attempt_id, "--kind", "plan", "--value", _put(root / "tmp" / "plan.json", plan)]
    # in this call: the plan's own pair first (#1), then the skeleton of the first batch (#2)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "skeleton-file-written" else "ARTIFACT_READ_BACK", None, 2)
        code, first = _main(capsys, argv)
        assert code == 2, first
    assert any((root / "imported-cases").glob(f"{attempt_id}.BATCH-*.json")), "the cut left the skeleton file"
    code, again = _main(capsys, argv)
    assert code == 0 and again["result"]["kind"] == "plan" and again["result"].get("batch_skeletons"), f"{point}: {again}"



def test_the_resume_is_for_the_request_the_code_wrote_itself(tmp_path: Path) -> None:
    """A request of the stage made by someone else (another invocation) is not resumed: MODEL_REQUEST_DUPLICATE, as before; the same
    request again is returned as it is, written once."""
    from tests.test_marker_parts import _run
    from tools import pilot_state

    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]]
    first = pilot_state.publish_code_step_request(root, attempt_id, "context-marker:baseline", model_id="code", invocation_id="merge-x", input_digests=inputs)
    again = pilot_state.publish_code_step_request(root, attempt_id, "context-marker:baseline", model_id="code", invocation_id="merge-x", input_digests=inputs)
    assert dict(first) == dict(again) and len(list((root / "model-requests" / attempt_id).glob("*.json"))) == 1
    with pytest.raises(pilot_state.ModelRequestDuplicate):
        pilot_state.publish_code_step_request(root, attempt_id, "context-marker:baseline", model_id="code", invocation_id="someone-else", input_digests=inputs)
