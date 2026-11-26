"""R&D-12, variant A: a part of the marker reads the view of the requirements answer (id, order, path:line, text), not the answer."""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_marker_parts import _publish_requirements, _run
from tools import pilot_state

PART = "context-marker:batch-001"
BLOCK = "### Requirement: Длина описания\nСистема SHALL принимать до 255 символов."
OPENSPEC_ROW = {"source_requirement_id": "SREQ-0001", "display_order": 1, "text": BLOCK, "digest": "sha256:" + "a" * 64,
                "provenance": [f"openspec/specs/visits/spec.md — Длина описания: {BLOCK}", "openspec/specs/visits/spec.md — sha256:" + "a" * 64,
                               "openspec/specs/visits/spec.md:5 — capability=visits; ### Requirement: Длина описания"]}


def _answer(*rows: dict) -> dict:
    return {"artifacts": {"analytics_documentation": {"requirements": list(rows)}}}


def _view(answer: dict) -> dict:
    from tools.step_size import requirements_view_bytes

    return json.loads(requirements_view_bytes(answer, "sha256:" + "b" * 64))


def _asked_part(tmp_path: Path) -> tuple[Path, dict]:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    answer = _publish_requirements(root, attempt_id, baseline)
    pilot_state.publish_model_request(root, attempt_id, PART, model_id="model-context", invocation_id="part-1",
                                      input_digests=[baseline["inventory_digest"], receipts[0]["digest"], answer["content_digest"]])
    return root, answer


def test_view_row_is_id_order_at_text() -> None:
    view = _view(_answer(OPENSPEC_ROW))
    assert view["requirements"] == [["SREQ-0001", 1, "openspec/specs/visits/spec.md:5", BLOCK]]
    assert "provenance" not in json.dumps(view["requirements"]) and "sha256:" + "a" * 64 not in json.dumps(view["requirements"])


def test_view_at_is_null_without_line() -> None:
    assert _view(_answer({**OPENSPEC_ROW, "provenance": ["docs/a.md — раздел"]}))["requirements"][0][2] is None


def test_part_reads_the_view(tmp_path: Path) -> None:
    from tools.step_size import REQUIREMENTS_VIEW, stage_inputs

    root, _answer = _asked_part(tmp_path)
    files = stage_inputs(root, PART)["files"]
    assert [row["basis"] for row in files if row.get("basis") == REQUIREMENTS_VIEW] == [REQUIREMENTS_VIEW]
    assert not any("model-stage-artifacts" in row["path"] for row in files)


def test_old_part_reads_the_answer(tmp_path: Path) -> None:
    from tools.step_size import requirements_view_path, stage_inputs

    root, answer = _asked_part(tmp_path)
    requirements_view_path(root, answer["content_digest"]).unlink()  # a part asked before the view: no view on the disk
    files = stage_inputs(root, PART, write_views=False)["files"]
    assert any(row["path"].endswith(answer["content_digest"].removeprefix("sha256:") + ".json") for row in files)


def test_plan_counts_the_view(tmp_path: Path) -> None:
    from tools.context_marker_parts import plan
    from tools.step_size import requirements_view_bytes

    root, attempt_id, baseline, _receipts = _run(tmp_path)
    answer = _publish_requirements(root, attempt_id, baseline)
    artifact = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer["content_digest"])["artifact"]
    part = next(row for row in plan(root)["parts"] if row["stage"] == PART)
    assert part["bytes"]["requirements"] == len(requirements_view_bytes(artifact, answer["content_digest"]))


def test_report_counts_the_view_as_markup(tmp_path: Path) -> None:
    from tools.run_report import _composition

    root, answer = _asked_part(tmp_path)
    found = {"files": [{"path": str(root / "marker-views" / "x.requirements-view.json"), "bytes": 10, "basis": "requirements-view-v1"}], "bindings": []}
    assert _composition(found, root.parent.parent, root)["markup"] == 10


def _old_run(tmp_path: Path) -> tuple[Path, dict]:
    root, attempt_id, baseline, _receipts = _run(tmp_path)
    return root, _publish_requirements(root, attempt_id, baseline)  # the requirements are answered, no part is asked: no view on the disk


def test_a_measurement_writes_no_view(tmp_path: Path) -> None:
    from tools.step_size import journal_inputs, requirements_view_path

    root, answer = _old_run(tmp_path)
    journal_inputs(root.parent.parent, root.name, "context-marker", 1)
    assert not requirements_view_path(root, answer["content_digest"]).exists()


def test_a_measurement_counts_the_view_it_does_not_write(tmp_path: Path) -> None:
    from tools.step_size import journal_inputs, requirements_view_bytes

    root, answer = _old_run(tmp_path)
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    artifact = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer["content_digest"])["artifact"]
    files, held = journal_inputs(root.parent.parent, root.name, "context-marker", 1)
    assert held == len(requirements_view_bytes(artifact, answer["content_digest"]))
    assert not any("model-stage-artifacts" in str(path) for path in files)


def test_an_answer_outside_the_run_is_not_read(tmp_path: Path) -> None:
    from tools.step_size import _requirements_view_data

    root, answer = _old_run(tmp_path)
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    stored = root / "model-stage-artifacts" / attempt_id / (answer["content_digest"].removeprefix("sha256:") + ".json")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(stored.read_bytes())
    assert _requirements_view_data(root.parent.parent, root, answer["content_digest"], elsewhere) is None
