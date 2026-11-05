"""PT2 (Petclinic v0.3): the shape of the inputs of ``plan`` named in its refusals and its text, ``<run>/tmp`` made by the scan,
compact answers of the commands that list a hundred files, no commands of mode B in the delivery of cases."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "skills" / "orchestrate" / "references" / "fragment-sample.md"
AUTOMATION_COMMANDS = ("automation-boundary-facts", "open-automation-boundary", "automation-digest", "relations-digest", "validate-automation", "runner-compat")


# ---- plan: the inputs ------------------------------------------------------------------------------------------------

def _sample_blocks() -> list:
    text = SAMPLE.read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)]


def test_the_sample_forms_of_the_plan_inputs_are_what_the_code_takes() -> None:
    from tests.test_batch_assembly import _header
    from tools import batch_assembly

    _fragment, _case, header, requirements, grouping = _sample_blocks()
    real = _header()
    assert set(header) == set(real) and set(header["metadata"]) == set(real["metadata"]) and set(header["metadata"]["subject"]) == set(real["metadata"]["subject"])
    digest = "sha256:" + "a" * 64
    filled = json.loads(json.dumps(requirements).replace("<64 hex>", "b" * 64))
    assert isinstance(filled, list) and batch_assembly._source_requirements([{**row, "text": "текст", "provenance": ["docs/feature.md:1-1"], "digest": digest}
                                                                              for row in filled])
    assert set(grouping) == {"context_receipt_digest", "context_receipt_digests"}
    assert batch_assembly._evidence_digests({"header_digest": digest, **{key: (digest if key == "context_receipt_digest" else [digest]) for key in grouping}})[1] == digest
    assert "`header_digest` код ставит сам" in SAMPLE.read_text(encoding="utf-8")


def test_the_skill_points_at_the_forms_and_the_heading_exists() -> None:
    skill = " ".join((ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8").split())
    assert "формы входов — [fragment-sample.md](references/fragment-sample.md#входы-plan)" in skill
    assert re.search(r"^## Входы `plan`$", SAMPLE.read_text(encoding="utf-8"), re.M)


def _grouping(digest: str, **extra) -> dict:
    return {"header_digest": digest, **extra}


@pytest.mark.parametrize(("case", "field", "code"), [
    ("whole marker answer as requirements", "--requirements", "BATCH_PARTITION_CONFLICT"),
    ("no context_receipt_digest", "--grouping", "BATCH_SEMANTIC_CONFLICT"),
    ("digests list does not start with the digest", "--grouping", "BATCH_PARTITION_CONFLICT"),
])
def test_a_refusal_about_the_shape_of_plan_inputs_names_the_command_the_input_and_the_reason(case: str, field: str, code: str) -> None:
    from tests.test_batch_assembly import _requirements
    from tools import api, batch_assembly
    from tools.json_cli import error_envelope

    digest = "sha256:" + "a" * 64
    other = "sha256:" + "b" * 64
    calls = {
        "whole marker answer as requirements": lambda: batch_assembly.plan_batches({"source_requirements": _requirements()}, _grouping(digest, context_receipt_digest=digest),
                                                                                  window_bytes=10_000, input_bytes=1000),
        "no context_receipt_digest": lambda: batch_assembly.plan_batches(_requirements(), _grouping(digest), window_bytes=10_000, input_bytes=1000),
        "digests list does not start with the digest": lambda: batch_assembly.plan_batches(
            _requirements(), _grouping(digest, context_receipt_digest=digest, context_receipt_digests=[other, digest]), window_bytes=10_000, input_bytes=1000),
    }
    with pytest.raises(batch_assembly.BatchAssemblyError) as refused:
        api._plan_inputs("plan", calls[case])
    envelope = error_envelope(refused.value)
    assert envelope["code"] == code and envelope["fix"].startswith(f"plan: {field}"), envelope
    assert "фрагмент" not in envelope["fix"] and refused.value.detail in envelope["fix"], envelope  # not the fix of a batch fragment


def test_other_refusals_of_plan_keep_their_table_fix() -> None:
    from tools import api, batch_assembly

    def refuse():
        raise batch_assembly.BatchAssemblyError("BATCH_PARTITION_CONFLICT", "out_of_scope leaves no source requirement to plan")

    with pytest.raises(batch_assembly.BatchAssemblyError) as refused:
        api._plan_inputs("plan", refuse)
    assert not getattr(refused.value, "fix", None) or "--requirements" not in refused.value.fix


# ---- <run>/tmp is made by the scan -------------------------------------------------------------------------------------

def test_the_run_has_its_tmp_directory_from_the_start_and_out_writes_there(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_self_review import _run
    from tools.api import main

    run_root, _attempt = _run(tmp_path, "cases-only-v1", flag=False)
    assert (run_root / "tmp").is_dir() and not any((run_root / "tmp").iterdir())
    source = run_root / "tmp" / "document.json"
    source.write_text("{}", encoding="utf-8")
    code = main(["document-digest", "--document", str(source), "--run-root", str(run_root), "--out", str(run_root / "tmp" / "digest.json")])
    assert code == 0 and (run_root / "tmp" / "digest.json").is_file(), capsys.readouterr().out


def test_a_scan_creates_the_tmp_directory_of_its_run(tmp_path: Path) -> None:
    from tests.test_exec_split import _json
    from tests.test_first_run import _python_project

    project = _python_project(tmp_path, runtime=False)
    done = subprocess.run([sys.executable, "-m", "tools.run_pipeline", "scan", "--project", str(project), "--docs", "services/api/docs/feature.md", "--module", "nested",
                           "--profile", "cases-only-v1"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert done.returncode == 0, done.stdout + done.stderr
    run_id = _json(done.stdout)["run_id"]
    assert (project / ".pilot-runs" / run_id / "tmp").is_dir()


# ---- compact answers ---------------------------------------------------------------------------------------------------

def test_the_files_of_a_step_are_a_summary_and_the_path_of_the_list(tmp_path: Path) -> None:
    from tests.test_self_review import _run
    from tools import api
    from tools.step_size import compact_inputs

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    rows = [{"digest": "sha256:" + f"{index:064x}", "path": str(run_root / "inputs" / (f"file-{index:03d}-" + "x" * 60 + ".java")), "bytes": 1000 + index}
            for index in range(200)] + [{"digest": "sha256:" + "f" * 64, "path": str(run_root / "map.md"), "bytes": 5, "basis": "карта"}]
    found = {"stage": "tc-generator:BATCH-x", "attempt_id": attempt_id, "request_digest": "sha256:" + "e" * 64, "files": rows, "bindings": [], "unresolved": []}
    printed = json.dumps(api.plain(compact_inputs(run_root, "tc-generator:BATCH-x", found)), ensure_ascii=False)
    assert len(printed.encode("utf-8")) < 2500 < len(json.dumps(found, ensure_ascii=False).encode("utf-8")) // 10
    summary = json.loads(printed)["files_summary"]
    assert summary["count"] == 201 and summary["bytes"] == sum(row["bytes"] for row in rows) and summary["by_basis"] == {"файл": 200, "карта": 1}
    assert len(summary["head"]) == 5 and "files" not in json.loads(printed)
    listed = json.loads(Path(summary["list_path"]).read_text(encoding="utf-8"))
    assert listed["files"] == rows and Path(summary["list_path"]).parent == run_root / "tmp"


def test_the_composition_of_prior_capabilities_is_listed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import pilot_state

    capabilities = [{"capability_id": f"CAP-B1-name{index}", "action": f"name{index}"} for index in range(80)]
    value = {"operation_capabilities": capabilities, "digest": "sha256:" + "d" * 64}
    run = tmp_path / "project" / ".pilot-runs" / ("a" * 32)
    run.mkdir(parents=True)
    monkeypatch.setattr(pilot_state, "_run_root", lambda run_root: (tmp_path / "project", run))
    monkeypatch.setattr(pilot_state, "derive_state", lambda root: {"events": []})
    monkeypatch.setattr(pilot_state, "_prior_capabilities_value", lambda *args: value)
    monkeypatch.setattr(pilot_state, "_receipt_target", lambda root, attempt, kind: run / "receipts" / "prior.json")
    monkeypatch.setattr(pilot_state, "_publish_bound_attempt_receipt", lambda *args: None)
    answer = pilot_state.publish_prior_capabilities(run, "b" * 32, "BATCH-x")
    assert answer["capabilities"] == 80 and answer["capability_ids"][0] == "CAP-B1-name0" and len(answer["capability_ids"]) == 60
    assert answer["capability_ids_more"] == 20 and answer["body_at"] == "record.prior_capabilities.operation_capabilities"
    assert answer["path"] == "receipts/prior.json" and len(json.dumps(answer)) < 3000


# ---- no commands of mode B in the delivery of cases ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("pt2") / "pack", "cases-only")


def _run_api(pack: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "tools.api", *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_the_help_of_the_cases_only_pack_lists_no_command_of_automation(cases_only_pack: Path) -> None:
    done = _run_api(cases_only_pack, "no-such-command", "--help")
    listing = done.stdout + done.stderr
    for command in AUTOMATION_COMMANDS:
        assert command not in listing, command
        refused = _run_api(cases_only_pack, command, "--help")
        assert refused.returncode == 2 and "invalid choice" in refused.stdout + refused.stderr, command
    for kept in ("validate-fragment", "review-open", "review-select", "plan"):  # F4b: the three commands of the nine are not commands of this delivery
        assert _run_api(cases_only_pack, kept, "--help").returncode == 0, kept


def test_the_repository_build_keeps_the_commands_where_the_execution_files_are() -> None:
    from tools import api, execution_gate

    assert execution_gate.execution_available() is True
    assert set(AUTOMATION_COMMANDS) <= set(api.COMMANDS)


def test_ready_says_finish_in_the_cases_only_row_and_exec_where_it_exists(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools.ready import check

    project = _python_project(tmp_path, runtime=False)
    cases = next(row for row in check(project, [], "cases-only-v1", "nested")["checks"] if row["id"] == "run:freeze")["message"]
    assert "до finish:" in cases and "exec" not in cases
    pilot = next(row for row in check(project, [], "local-pilot-v1", "nested")["checks"] if row["id"] == "run:freeze")["message"]
    assert "до finish или exec" in pilot


def test_doctor_of_the_cases_only_pack_prints_nothing_about_running_projects(cases_only_pack: Path) -> None:
    from tools.doctor import inspect_environment

    report = inspect_environment(cases_only_pack)
    assert report["status"] == "PASS" and "adapters" not in report and "languages" not in report
    assert "languages" in inspect_environment(ROOT) and "adapters" in inspect_environment(ROOT)


# ---- the three weak checks of the generator ------------------------------------------------------------------------------

def _jsonl_rows() -> list[dict]:
    text = SAMPLE.read_text(encoding="utf-8")
    return [json.loads(line) for block in re.findall(r"```jsonl\n(.*?)\n```", text, re.S) for line in block.split("\n") if line.strip()]


def test_the_three_samples_of_checks_are_what_the_schema_takes_and_say_what_each_one_checks(tmp_path: Path) -> None:
    import shutil

    from tools.schema_validation import schema_diagnostics

    rows = _jsonl_rows()
    results = [row for row in rows if "semantic_type" in row and "operator" not in row]
    assertions = [{**row, "assertion_id": "ASSERT-B1-001-01-1-1", "display_order": 1} for row in rows if "operator" in row]  # V6: the id and the order are the code's
    assert len(results) == 1 and len(assertions) == 3
    copy = tmp_path / "schemas"
    shutil.copytree(ROOT / "schemas", copy)

    def valid(name: str, definition: str, value: dict) -> list:
        target = copy / f"sample-{name}.schema.json"
        target.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"schemas/sample-{name}.schema.json",
                                      "$ref": f"canonical-test-document.schema.json#/$defs/{definition}"}), encoding="utf-8")
        return schema_diagnostics(json.loads(json.dumps(value, ensure_ascii=False).replace("<ns>", "B1").replace("<хвост case_id>", "B1-001")), target, tmp_path)

    assert valid("result", "capability_member", results[0]) == []
    assert [valid(f"assertion-{index}", "assertion", row) for index, row in enumerate(assertions)] == [[], [], []]
    delta, redirect, field = assertions
    assert delta["operator"] == "equals" and delta["actual"] == {"kind": "project_result", "name": "delta"} and delta["expected"] == {"kind": "literal", "value": 1}
    assert redirect["operator"] == "equals" and redirect["expected"]["kind"] == "step_output" and redirect["actual"]["name"] == "redirectId"
    assert field["operator"] == "equals" and field["expected"] == {"kind": "literal", "value": "telephone:typeMismatch"}
    text = SAMPLE.read_text(encoding="utf-8")
    assert "BLOCKING" in text and "manual_only" in text and "автоматизации" not in text.split("## Три проверки", 1)[1].split("## Как собрать", 1)[0]


# ---- before the verdict ----------------------------------------------------------------------------------------------------

def test_read_effective_before_the_verdict_is_an_answer_not_a_refusal_of_the_journal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_self_review import _run
    from tools.api import main

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    code = main(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id])
    answer = json.loads(capsys.readouterr().out)
    assert code == 0 and answer["status"] == "ok" and answer["result"]["effective"] is None and "вердикт" in answer["result"]["reason"], answer
    assert not (run_root / "refusals.jsonl").exists(), "no refusal was counted"
    code = main(["read-effective", "--run-root", str(run_root), "--attempt", "f" * 32])
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and refused["status"] == "error", "an attempt that does not exist is still refused"


def test_the_report_of_a_run_without_an_effective_document_counts_the_candidate_and_says_so(tmp_path: Path) -> None:
    from tests.test_review_requested import _generated_run
    from tools.run_report import build_report, render_markdown

    run = _generated_run(tmp_path)
    report = build_report(tmp_path / "project", Path(run["run_root"]).name)[0]
    counts = report["counts"]
    assert counts["cases"] and counts["canonical_requirements"] and counts["canon_basis"] == "кандидат r1, не принят", counts
    assert "кандидат r1, не принят" in render_markdown(report)


# ---- one wording about the agent's own scripts ---------------------------------------------------------------------------------

def test_the_three_texts_say_the_same_about_scripts_and_none_contradicts() -> None:
    sentence = "Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши."
    skill = " ".join((ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8").split())
    sample = " ".join(SAMPLE.read_text(encoding="utf-8").split())
    contract = " ".join((ROOT / "skills" / "tc-generator" / "references" / "case-generation-contract.md").read_text(encoding="utf-8").split())
    assert sentence in skill and sentence in sample
    assert "write no scripts that import or call the package" in contract and "write no scripts of your own" not in contract
    for text in (skill, sample, contract):
        assert "своих скриптов" not in text and "допустим собственный скрипт" not in text.lower() and "can write a script" not in text.lower()


# ---- "no effective document yet" is decided by the journal, not by a missing file ------------------------------------------------

def test_a_deleted_effective_document_is_a_refusal_not_no_verdict_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The probe of orchestrator-3: the run has its accepted effective selection; its file is gone; the journal still says it was accepted."""
    from tests.test_finish import _project
    from tools import api, pilot_state

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    assert api.main(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id]) == 0
    assert json.loads(capsys.readouterr().out)["result"].get("effective", "x") is not None
    pilot_state._receipt_target(Path(run_root), attempt_id, "effective-canonical").unlink()
    code = api.main(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id])
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and refused["status"] == "error", refused


@pytest.mark.slow
def test_a_rejecting_verdict_says_so_instead_of_no_verdict_yet() -> None:
    from tests.test_review_requested import _api as call, _complete, _reviewed, _select
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        run, review = _reviewed(Path(directory), "ТРЕБУЕТ ДОРАБОТКИ")
        assert _complete(run)[0] == 0 and _select(run, review)[0] == 0
        code, answer = call("read-effective", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
        assert code == 0 and answer["result"]["effective"] is None, answer
        assert "REJECTED" in answer["result"]["reason"] and "не принимает кандидата" in answer["result"]["reason"] and "не записан" not in answer["result"]["reason"]
