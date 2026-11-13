"""FaT-ER: refusals that say what is wrong and give the way out; the checks themselves stay as they are.

(а) scan --module with a module root instead of its id: MODULE_UNKNOWN with the ids and their roots;
(б) finish after the canonical reviewer answered but before select: the select command of this run;
(в) the orchestrate SKILL names the directory of the set (``<run>/published``) and no flag for it.
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


def test_finish_after_the_review_answer_but_before_the_selection_names_submit(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools import run_pipeline

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    with pytest.raises(run_pipeline.HostStop) as stop:
        run_pipeline.cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name, abandon=None))
    assert stop.value.code == "FINISH_FACT_UNDETERMINED" and "submit" in str(stop.value)
    fix = stop.value.fix
    # FaT-O12b: submit takes the package inputs and the reviewer answer from the journal
    assert "tools.api submit" in fix and "--stage tc-reviewer:canonical" in fix and "orchestrate_test_case_revision" not in fix
    assert str(run_root) in fix and "--output-dir" not in fix and "--verdict" not in fix  # CO1: commands of the delivery


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


def test_the_orchestrate_skill_names_the_directory_of_the_set_and_no_flag_for_it() -> None:
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    # V4: the set of the review goes to `<run>/published` by itself — the agent names no directory
    flat = " ".join(text.split())
    assert "кладёт набор в `<run>/published`" in flat and "--output-dir" not in flat.split("## 4.", 1)[1].split("## 5.", 1)[0]
