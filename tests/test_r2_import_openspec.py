"""FaT-R2 (audit 2, 🔴): an import (``scan --cases``, no ``--docs``) on a project with OpenSpec in its context — the
requirements code builds from the CSV are its cases, never reconciled with OpenSpec; the import by code checks its answer
before it writes the request, and a refusal has a code."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cases_import import _project, _scan

SPEC = ("# sample Specification\n\n## Purpose\nCombine values.\n\n## Requirements\n### Requirement: Combine\n"
        "The system SHALL combine two values with a colon.\n\n#### Scenario: two values\n- **WHEN** combine(\"a\", \"b\")\n- **THEN** \"a:b\"\n")


def _openspec_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    from tools.pilot_state import derive_state, read_context_selection

    project = _project(tmp_path)
    (project / "openspec" / "specs" / "sample").mkdir(parents=True)
    (project / "openspec" / "specs" / "sample" / "spec.md").write_bytes(SPEC.encode("utf-8"))
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    root = Path(scan["run_root"])
    paths = [item["project_path"].replace("\\", "/") for event in derive_state(root)["events"] if event["event_type"] == "CONTEXT_SELECTED"
             for item in read_context_selection(root, scan["attempt_id"], event["artifact_digest"])["files"]]
    assert "openspec/specs/sample/spec.md" in paths
    return root, scan["attempt_id"]


def test_an_import_on_a_project_with_openspec_marks_and_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import api, context_marker_parts, pilot_state

    root, attempt_id = _openspec_run(tmp_path, monkeypatch)
    assert api.main(["prepare", "--run-root", str(root), "--stage", "context-marker:requirements"]) == 0
    answer = next(event["artifact_digest"] for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    value = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer)["artifact"]
    for part in [row for row in context_marker_parts.plan(root)["parts"] if row["stage"].startswith("context-marker:batch-")]:
        pilot_state.publish_model_request(root, attempt_id, part["stage"], model_id="model-context", invocation_id=part["stage"][-3:],
                                          input_digests=part["input_digests"])
        pilot_state.publish_model_stage_artifact(root, attempt_id, part["stage"], {
            **value, "artifacts": {**value["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine"]}}})
    merged = context_marker_parts.merge_parts(root)
    assert merged["artifact"]["artifacts"]["analytics_documentation"]["requirements"] == value["artifacts"]["analytics_documentation"]["requirements"]
    assert context_marker_parts.plan(root)["next"] == "done"


def test_a_refused_code_answer_leaves_no_request_and_names_its_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.cases_import import CasesImportError, import_requirements
    from tools.json_cli import error_fields

    root, attempt_id = _openspec_run(tmp_path, monkeypatch)

    def refuse(_stage, _value):
        raise ValueError("SOURCE_REQUIREMENT_ORDER: context-marker:requirements: порядок")

    monkeypatch.setattr(pilot_state, "check_model_stage_artifact", refuse)
    with pytest.raises(CasesImportError) as refused:
        import_requirements(root, attempt_id)
    fields = error_fields(refused.value)
    assert fields["code"] == "IMPORT_REQUIREMENTS_INVALID" and "--transport-attempts" not in fields["fix"], fields
    assert not [event for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "MODEL_REQUESTED"]
    monkeypatch.undo()
    assert import_requirements(root, attempt_id)["content_digest"]  # nothing orphaned: the import goes on


def test_the_openspec_refusal_of_a_model_answer_has_a_code(tmp_path: Path) -> None:
    from tests.test_au5_single_checks import _request_requirements, _spec_run
    from tests.test_marker_parts import _marker
    from tools import pilot_state
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
    from tools.json_cli import error_fields

    root, attempt_id, baseline, _receipts = _spec_run(tmp_path)
    _request_requirements(root, attempt_id, baseline)
    with pytest.raises(ValueError) as refused:
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _marker([REQUIREMENTS_PLACEHOLDER]))
    fields = error_fields(refused.value)
    assert fields["code"] == "CONTEXT_MARKER_OPENSPEC" and "--transport-attempts 2" in fields["fix"], fields


def test_a_model_answer_under_the_label_of_the_import_is_still_reconciled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The key of the skip is the CSV bytes, not the label: an answer that is not the code's one meets OpenSpec as any."""
    from tests.test_marker_parts import _marker
    from tools import pilot_state
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
    from tools.json_cli import error_fields
    from tools.project_inventory import read_execution_baseline

    root, attempt_id = _openspec_run(tmp_path, monkeypatch)
    attempt = pilot_state.derive_state(root)["attempts"][-1]
    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    pilot_state.publish_model_request(root, attempt_id, "context-marker:requirements", model_id="controller:cases-import", invocation_id="forged",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])
    with pytest.raises(ValueError) as refused:
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _marker([REQUIREMENTS_PLACEHOLDER]))
    assert error_fields(refused.value)["code"] == "CONTEXT_MARKER_OPENSPEC", refused.value
