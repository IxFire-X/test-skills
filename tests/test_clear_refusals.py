"""FaT-ER: refusals that say what is wrong and give the way out; the checks themselves stay as they are.

(а) scan --module with a module root instead of its id: MODULE_UNKNOWN with the ids and their roots;
(б) finish after the canonical reviewer answered but before select: the select command of this run;
(в) select with another --output-dir than publish: both directories, not only «Orchestration failed.»;
(г) the orchestrate SKILL names the --output-dir of select as the directory of publish.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_a_module_root_instead_of_its_id_is_module_unknown_with_the_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    code, payload = _scan(_python_project(tmp_path, runtime=False), monkeypatch, module="services/api")
    assert code == 2 and payload["reason"] == "MODULE_UNKNOWN", payload
    assert "nested (root services/api)" in payload["detail"] and "services/api" in payload["detail"]
    assert payload["fix"] == "--module nested"


def test_finish_after_the_review_answer_but_before_select_names_select(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools import run_pipeline

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    with pytest.raises(run_pipeline.HostStop) as stop:
        run_pipeline.cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name, abandon=None))
    assert stop.value.code == "FINISH_FACT_UNDETERMINED" and "select" in str(stop.value)
    fix = stop.value.fix
    assert "tools.orchestrate_test_case_revision select" in fix and f"--attempt-id {attempt_id}" in fix
    assert str(run_root) in fix and "complete-review" in fix and "--review " in fix and str(run_root / "model-stage-artifacts") in fix


def test_select_with_another_output_dir_names_the_directory_of_publish(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    published = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=published)
    other = run_root / "elsewhere"
    with pytest.raises(OrchestrationError) as refused:
        orchestrate_revision(document, receipt, package, session, review, other, run_root=run_root, attempt_id=attempt_id)
    assert refused.value.code == "ORCHESTRATION_RECEIPT"
    [row] = refused.value.diagnostics
    assert row["code"] == "ORCHESTRATION_OUTPUT_DIR" and str(other.resolve()) in row["message"]
    assert f"--output-dir {published.resolve()}" in row["message"]


def test_the_same_directory_with_other_files_stays_a_receipt_refusal(tmp_path: Path) -> None:
    """The receipt names other files in the same directory (another candidate or revision): no claim about the directory."""
    import dataclasses

    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    published = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=published)
    renamed = dataclasses.replace(receipt, json_path=str(published.resolve() / "other.r1.json"))
    with pytest.raises(OrchestrationError) as refused:
        orchestrate_revision(document, renamed, package, session, review, published, run_root=run_root, attempt_id=attempt_id)
    assert refused.value.code == "ORCHESTRATION_RECEIPT"
    assert [row["code"] for row in refused.value.diagnostics] == ["ORCHESTRATION_RECEIPT"]


def test_the_orchestrate_skill_names_the_output_dir_of_select() -> None:
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "`select` — тот же `--output-dir`, что у `publish`" in text
