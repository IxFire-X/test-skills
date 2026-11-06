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

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]


def test_a_module_root_is_taken_as_the_module_of_that_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    project = _python_project(tmp_path, runtime=False)
    by_id = _scan(project, monkeypatch, module="nested")[1]
    code, by_root = _scan(project, monkeypatch, module="services/api")
    assert code == 0 and by_root["status"] == "ok", by_root
    assert by_root["module_id"] == by_id["module_id"] == "nested"
    assert by_root["scope"]["module"] == by_id["scope"]["module"] == "services/api"


def test_an_unknown_module_is_module_unknown_with_the_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    code, payload = _scan(_python_project(tmp_path, runtime=False), monkeypatch, module="nope")
    assert code == 2 and payload["reason"] == "MODULE_UNKNOWN", payload
    assert "nested (root services/api)" in payload["detail"]
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
    # FaT-O12b: tools.api review-select takes the package inputs and the reviewer answer from the journal
    assert "tools.api review-select" in fix and f"--attempt {attempt_id}" in fix and "orchestrate_test_case_revision" not in fix
    assert str(run_root) in fix and "complete-review" not in fix and "--reviewer-session" not in fix and "--generator-fragment" not in fix  # CO1: commands of the delivery


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
    from tools.run_pipeline import command_token

    assert f"--output-dir {command_token(str(published.resolve()))}" in row["message"]


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
    # FaT-U5: review-select takes the --output-dir review-publish published to
    # PT: one paragraph names the one directory of the whole review (`<run>/published`) for review-open, review-select and review-publish
    flat = " ".join(text.split())
    assert "**`--output-dir`** — один каталог на всё ревью" in flat and "`<run>/published`" in flat
    assert "review-publish --run-root <run> --attempt <id> --output-dir <dir>" in flat and "review-select --run-root <run> --attempt <id> --output-dir <dir>" in flat
