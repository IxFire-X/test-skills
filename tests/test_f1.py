"""F1 «правда отчёта и status» (bug hunt 8: 8.1, 8.3, 8.4, 8.5; arch review 3: A1-1, A1-3, A1-7): the report takes the case document only
from the journal; the size of an answer never drops under the stored artifact; the number of refusals says it comes from a file;
read-receipt before the boundary says «not yet» without a refusal; a receipt of the journal without its file is named by status
and the report."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run, _put
from tests.test_review_two_commands import _events, _flags, _open, _select_verdict, _verdict


def _main(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _pipeline(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import run_pipeline

    code = run_pipeline.main(argv)
    out = capsys.readouterr().out
    body = out.rsplit("\nrun_id: ", 1)[0] if "\nrun_id: " in out else out
    return code, json.loads(body.strip())


def _report(run: dict) -> dict:
    from tools.run_report import build_report

    return build_report(run["project"], Path(run["run_root"]).name)[0]


def _opened(tmp_path: Path) -> dict:
    """A run with the candidate published and the review open (no verdict yet) — the shape of the report before a verdict."""
    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0, opened
    return run


def _candidate_digest(run: dict) -> str:
    event = next(row for row in reversed(_events(run["run_root"])) if row["event_type"] == "CANDIDATE_PUBLISHED" and row.get("stage_instance_id") == "assembly")
    return str(event["artifact_digest"])


def _forged_document(run: dict, factor: int = 50) -> dict:
    from tools.orchestrate_test_case_revision import journal_assembly

    document = json.loads(json.dumps(journal_assembly(run["run_root"], run["attempt_id"])[0]))
    document["test_cases"] = document["test_cases"] * factor
    return document


# --- 8.1 / A1-3 -----------------------------------------------------------------------------------------------------

def test_the_report_counts_the_journal_candidate_not_a_planted_file_named_by_its_digest(tmp_path: Path) -> None:
    run = _opened(tmp_path)
    before = _report(run)["counts"]
    assert before["cases"] and before["canon_basis"] == "кандидат r1, не принят", before
    _put(Path(run["run_root"]) / "tmp" / (_candidate_digest(run).removeprefix("sha256:") + ".json"), _forged_document(run))
    after = _report(run)["counts"]
    assert after["cases"] == before["cases"] and after["canonical_requirements"] == before["canonical_requirements"], (before, after)
    assert after["canon_basis"] == before["canon_basis"], (before, after)


def test_the_report_counts_the_journal_candidate_not_a_planted_case_sets_folder(tmp_path: Path) -> None:
    run = _opened(tmp_path)
    before = _report(run)["counts"]
    _put(Path(run["run_root"]) / "case-sets" / "r7" / "planted.json", _forged_document(run))
    after = _report(run)["counts"]
    assert after["cases"] == before["cases"] and after["canon_basis"] == before["canon_basis"], (before, after)


def test_an_effective_file_that_cannot_be_read_is_named_and_gives_no_other_document(tmp_path: Path) -> None:
    run = _opened(tmp_path)
    planted = Path(run["run_root"]) / "effective-canonicals" / f"{run['attempt_id']}.json"
    planted.parent.mkdir(exist_ok=True)
    planted.write_text("{не json", encoding="utf-8")
    _put(Path(run["run_root"]) / "case-sets" / "r7" / "planted.json", _forged_document(run))
    report = _report(run)
    assert report["counts"]["cases"] is None, report["counts"]
    assert any("эффективный канон не читается" in str(item) for item in report["warnings"]), report["warnings"]


def test_the_case_document_function_reads_nothing_but_the_journal(tmp_path: Path) -> None:
    from tools.run_report import case_document

    run = _opened(tmp_path)
    _put(Path(run["run_root"]) / "case-sets" / "r1" / "planted.json", _forged_document(run))
    assert case_document(Path(run["run_root"]), run["attempt_id"]) is None  # before the verdict the journal has no effective document


# --- 8.3 --------------------------------------------------------------------------------------------------------------

def _batch_request(run: dict, index: int) -> tuple[str, Path]:
    from tools.pilot_state import publish_generator_input, publish_model_request

    header = json.loads(Path(run["header"]).read_bytes())
    plan = json.loads(Path(run["plan"]).read_bytes())
    context = json.loads(Path(run["context"]).read_bytes())
    fragment = json.loads(Path(run["fragments"][index]).read_bytes())
    if index == 0:
        publish_generator_input(run["run_root"], run["attempt_id"], "header", header)
        publish_generator_input(run["run_root"], run["attempt_id"], "plan", plan)
    stage = f"tc-generator:{fragment['batch_id']}"
    publish_model_request(run["run_root"], run["attempt_id"], stage, model_id="model-generator", invocation_id=f"invoke-{index + 1}",
                          input_digests=[run["marker_digest"], context["digest"], fragment["plan_digest"], fragment["header_digest"]])
    return stage, Path(run["fragments"][index])


def test_an_edited_output_sizes_file_does_not_silence_step_output_large(tmp_path: Path, capsys, monkeypatch) -> None:
    from tools import step_size

    monkeypatch.setattr(step_size, "DEFAULT_OUTPUT_WARN", 10)
    run = _generated_run(tmp_path, generated=False)
    stage, fragment = _batch_request(run, 0)
    code, answered = _main(capsys, ["model-response", *_flags(run), "--stage", stage, "--artifact", str(fragment)])
    assert code == 0 and answered["result"]["warning"] == "STEP_OUTPUT_LARGE" and answered["result"]["output_basis"] == "raw", answered
    assert "STEP_OUTPUT_LARGE" in _report(run)["warnings"]
    sizes = Path(run["run_root"]) / "output-sizes.jsonl"
    rows = [json.loads(line) for line in sizes.read_text(encoding="utf-8").splitlines()]
    sizes.write_text("".join(json.dumps({**row, "raw_bytes": 1}, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report = _report(run)
    call = next(row for row in report["model_calls"] if row["stage"] == stage)
    assert "STEP_OUTPUT_LARGE" in report["warnings"] and call["output_large"] is True, (report["warnings"], call)
    assert call["output_basis"] == "compact", call  # a raw size under the stored artifact is not believed


# --- 8.4 / A1-1 -------------------------------------------------------------------------------------------------------

def test_the_number_of_refusals_says_it_comes_from_a_file_the_journal_does_not_confirm(tmp_path: Path) -> None:
    from tools.run_report import render_markdown

    run = _opened(tmp_path)
    code, refused = _api("read-effective", "--run-root", str(run["run_root"]), "--attempt", "f" * 32)
    assert code == 2, refused
    report = _report(run)
    assert report["refusals"]["total"] == 1
    assert report["refusals"]["basis"] == "по файлу refusals.jsonl, журналом не подтверждено", report["refusals"]
    assert "по файлу refusals.jsonl, журналом не подтверждено" in render_markdown(report)


# --- read-receipt before the boundary -----------------------------------------------------------------------------------

def test_read_receipt_of_the_boundary_before_review_open_is_not_yet_and_not_a_refusal(tmp_path: Path, capsys) -> None:
    run = _generated_run(tmp_path)
    root = Path(run["run_root"])
    code, answer = _main(capsys, ["read-receipt", "--run-root", str(root), "--attempt", run["attempt_id"], "--kind", "reviewer-session-boundary",
                                  "--event", "ARTIFACT_READ_BACK"])
    assert code == 0 and answer["status"] == "ok", answer
    assert answer["result"]["receipt"] is None and "ещё нет" in answer["result"]["reason"], answer
    assert not (root / "refusals.jsonl").exists()


def test_read_receipt_of_the_boundary_after_review_open_is_the_receipt_as_before(tmp_path: Path, capsys) -> None:
    run = _opened(tmp_path)
    code, answer = _main(capsys, ["read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "reviewer-session-boundary",
                                  "--event", "ARTIFACT_READ_BACK"])
    assert code == 0 and answer["result"]["record"]["kind"] == "reviewer-session-boundary" and answer["result"]["digest"], answer


def test_read_receipt_of_a_boundary_the_journal_knows_but_whose_file_is_gone_is_a_refusal_with_a_code_and_a_fix(tmp_path: Path, capsys) -> None:
    run = _opened(tmp_path)
    next((Path(run["run_root"]) / "reviewer-session-boundaries").glob("*.json")).unlink()
    code, answer = _main(capsys, ["read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "reviewer-session-boundary",
                                  "--event", "ARTIFACT_READ_BACK"])
    assert code == 2 and answer["code"] not in ("ValueError", None) and answer.get("fix"), answer  # never «not yet»


def test_read_receipt_of_another_kind_without_its_file_is_a_coded_refusal(tmp_path: Path, capsys) -> None:
    run = _generated_run(tmp_path)
    code, answer = _main(capsys, ["read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "effective-canonical",
                                  "--event", "ARTIFACT_READ_BACK"])
    assert code == 2 and answer["code"] not in ("ValueError", None) and answer.get("fix"), answer


# --- 8.5 / A1-7 -------------------------------------------------------------------------------------------------------

def _gone(run: dict, kind: str) -> Path:
    root = Path(run["run_root"])
    if kind == "marker":
        return root / "model-stage-artifacts" / run["attempt_id"] / (run["marker_digest"].removeprefix("sha256:") + ".json")
    if kind == "ledger":
        return next((root / "reviewer-session-ledgers" / run["attempt_id"]).glob("*.json"))
    if kind == "context":
        return next((root / "context-selections" / run["attempt_id"]).glob("*.json"))
    if kind == "boundary":
        return next((root / "reviewer-session-boundaries").glob("*.json"))
    if kind == "header":
        return root / "generator-headers" / f"{run['attempt_id']}.json"
    return root / "batch-plans" / f"{run['attempt_id']}.json"


@pytest.mark.parametrize("kind", ["marker", "ledger", "context", "boundary", "plan", "header"])
def test_a_receipt_of_the_journal_without_its_file_is_named_by_status_and_the_report(tmp_path: Path, capsys, kind: str) -> None:
    run = _opened(tmp_path)
    root = Path(run["run_root"])
    target = _gone(run, kind)
    assert target.is_file(), target
    target.unlink()
    code, status = _pipeline(capsys, ["status", "--project", str(run["project"]), "--run", root.name])
    assert status.get("evidence_errors"), {key: status.get(key) for key in ("status", "warnings", "evidence_errors", "stop_reason")}
    report = _report(run)
    assert report["stops"]["evidence_errors"], report["stops"]


@pytest.mark.parametrize("kind", ["plan", "header"])
def test_a_plan_or_header_not_yet_published_is_not_an_evidence_error(tmp_path: Path, capsys, kind: str) -> None:
    run = _generated_run(tmp_path, generated=False)
    root = Path(run["run_root"])
    assert not _gone(run, kind).exists()
    code, status = _pipeline(capsys, ["status", "--project", str(run["project"]), "--run", root.name])
    assert not status.get("evidence_errors"), status


def test_the_effective_file_removed_after_an_accepting_verdict_is_named_by_status(tmp_path: Path, capsys) -> None:
    run = _opened(tmp_path)
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0, selected
    root = Path(run["run_root"])
    effective = root / "effective-canonicals" / f"{run['attempt_id']}.json"
    assert effective.is_file()
    effective.unlink()
    code, status = _pipeline(capsys, ["status", "--project", str(run["project"]), "--run", root.name])
    assert any("effective-canonicals" in str(row.get("path")) for row in status.get("evidence_errors") or []), status


def test_an_unreadable_published_plan_never_gives_the_scope_to_the_decision_file(tmp_path: Path) -> None:
    run = _opened(tmp_path)
    root = Path(run["run_root"])
    _put(root / "scope-decision.json", {"decision": "exclude", "excluded": [{"source_requirement_id": "SREQ-payment", "reason": "x"}]})
    _gone(run, "plan").unlink()
    scope = _report(run).get("scope")
    assert scope is not None and scope.get("refused", {}).get("code") == "SCOPE_PLAN_UNREADABLE" and scope["refused"]["fix"], scope
    assert not scope.get("out_of_scope"), scope


def test_an_accepted_verdict_whose_effective_file_is_gone_is_not_reported_as_the_candidate(tmp_path: Path) -> None:
    from tests.test_finish import _project
    from tools import pilot_state

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    before = _report_of(_project_path, run_root)
    assert before["counts"]["cases"] and before["counts"]["canon_basis"] is None, before["counts"]
    pilot_state._receipt_target(Path(run_root), attempt_id, "effective-canonical").unlink()
    after = _report_of(_project_path, run_root)
    assert any("EFFECTIVE_CANON_UNREADABLE" in str(item) for item in after["warnings"]), after["warnings"]
    assert after["counts"]["cases"] is None and after["counts"]["canon_basis"] != "кандидат r1, не принят", after["counts"]


def _report_of(project: Path, run_root: Path) -> dict:
    from tools.run_report import build_report

    return build_report(project, Path(run_root).name)[0]


def test_a_planted_receipt_with_the_right_digest_field_but_another_body_does_not_hide_the_loss(tmp_path: Path, capsys) -> None:
    run = _opened(tmp_path)
    root = Path(run["run_root"])
    target = _gone(run, "plan")
    digest = json.loads(target.read_text(encoding="utf-8"))["digest"]
    target.write_text(json.dumps({"digest": digest, "plan": "подложено"}, ensure_ascii=False), encoding="utf-8")
    code, status = _pipeline(capsys, ["status", "--project", str(run["project"]), "--run", root.name])
    assert status.get("evidence_errors"), {key: status.get(key) for key in ("status", "warnings", "evidence_errors", "stop_reason")}
