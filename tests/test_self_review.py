"""FaT-4b: a review without a fresh context is accepted only by an explicit human choice, `scan --accept-self-review`.

Every place that checks reviewer isolation is tested twice: with the flag the review is admitted and marked SELF,
without it the prior refusal stays.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_exit_policy import _local
from tools import run_pipeline

RECEIPT_KEYS = {"schema_version", "run_id", "request_id", "policy_profile", "execution_requested", "digest"}


def _run(tmp_path: Path, profile: str = "local-pilot-v1", *, flag: bool) -> tuple[Path, str]:
    from tests.helpers import build_phase_two_baseline
    from tools.run_pipeline import run_phase_one_spine

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": profile}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    authorization = {"request_id": "self-review", "execution_requested": profile == "local-pilot-v1"}
    if flag:
        authorization["accept_self_review"] = True
    run = run_phase_one_spine(tmp_path, profile, authorization, identity, baseline, {})
    return Path(run["run"]["run_root"]), run["state"]["attempts"][0]["attempt_id"]


def _reviewed(tmp_path: Path, profile: str = "local-pilot-v1", *, flag: bool, fresh_context: bool = False):
    """A canonical review in the same session (fresh_context false), then the selection."""
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tools.orchestrate_test_case_revision import orchestrate_revision

    run_root, attempt_id = _run(tmp_path, profile, flag=flag)
    document = automated_document()
    bundle_root = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(
        document, run_root, attempt_id, bundle_root=bundle_root, fresh_context=fresh_context)
    selected = orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    return run_root, attempt_id, document, selected


def _automation_boundary(run_root: Path, attempt_id: str, document: dict, *, fresh_context: bool):
    from tests.test_automation_revision_budget import automation, durable_boundary, host_evidence
    from tools.pilot_state import read_effective_canonical

    effective = read_effective_canonical(run_root, attempt_id)
    artifact = automation(document, effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"])
    evidence = host_evidence("auto-review", generator="auto-generator", reviewer="auto-reviewer") | {"fresh_context": fresh_context}
    _root, _attempt, boundary = durable_boundary(
        run_root.parents[1], artifact, "auto-review", evidence, run_root=run_root, attempt_id=attempt_id,
        generator="auto-generator", reviewer="auto-reviewer")
    return artifact, boundary


# ------------------------------------------------------------------ the flag in the run


def test_a_run_without_the_flag_keeps_the_receipt_byte_for_byte(tmp_path: Path) -> None:
    from tools.pilot_state import read_run, self_review_accepted

    run_root, _attempt_id = _run(tmp_path, flag=False)
    assert set(read_run(run_root)["authorization"]) == RECEIPT_KEYS
    assert self_review_accepted(run_root) is False
    # FaT-WT: the bytes themselves, rebuilt by hand — the keys alone pass a changed value or a changed format
    import hashlib

    def canonical(value: dict) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"

    body = {"schema_version": "1.0.0", "run_id": run_root.name, "request_id": "self-review", "policy_profile": "local-pilot-v1",
            "execution_requested": True}
    expected = canonical({**body, "digest": "sha256:" + hashlib.sha256(canonical(body)).hexdigest()})
    assert (run_root / "run-authorization-receipt.json").read_bytes() == expected


def test_the_flag_is_sealed_in_the_receipt_and_survives_lowering(tmp_path: Path) -> None:
    from tools.pilot_state import create_lowered_run, read_run, self_review_accepted

    run_root, _attempt_id = _run(tmp_path / "a", flag=True)
    assert read_run(run_root)["authorization"]["accept_self_review"] is True and self_review_accepted(run_root) is True
    lowered = create_lowered_run(tmp_path / "a", {"request_id": "lowered", "execution_requested": False, "accept_self_review": True},
                                 {"reason": "сборки нет", "fix": "соберите модуль"})
    assert lowered["authorization"]["accept_self_review"] is True and lowered["manifest"]["policy_profile"] == "cases-only-v1"
    assert self_review_accepted(Path(lowered["run_root"])) is True


def test_the_flag_must_be_true_when_present(tmp_path: Path) -> None:
    from tools.pilot_state import create_run

    with pytest.raises(ValueError):
        create_run(tmp_path, "cases-only-v1", {"request_id": "x", "execution_requested": False, "accept_self_review": False})


def test_scan_and_status_show_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _args, _python_project

    project = _python_project(tmp_path, runtime=False)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.accept_self_review = True
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
    scan = payloads[-1]
    assert scan["self_review_accepted"] is True and scan["policy_profile"] == "cases-only-v1"
    authorization = json.loads((Path(scan["run_root"]) / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    assert authorization["accept_self_review"] is True
    run_pipeline.cmd_status(SimpleNamespace(project=str(project), run=scan["run_id"]))
    assert payloads[-1]["self_review_accepted"] is True


def test_scan_without_the_flag_does_not_mention_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _args, _python_project

    project = _python_project(tmp_path, runtime=False)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project)) == 0, payloads[-1]
    assert "self_review_accepted" not in payloads[-1]
    authorization = json.loads((Path(payloads[-1]["run_root"]) / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    assert set(authorization) == RECEIPT_KEYS


# ------------------------------------------------------------------ 1-2: selection and reviewer evidence


def test_without_the_flag_selection_refuses_an_unisolated_review(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import OrchestrationError

    with pytest.raises(OrchestrationError) as error:
        _reviewed(tmp_path, flag=False)
    assert "INDEPENDENCE_UNVERIFIED" in json.dumps(error.value.diagnostics if hasattr(error.value, "diagnostics") else str(error.value), default=str)


def test_with_the_flag_selection_accepts_and_the_evidence_is_marked(tmp_path: Path) -> None:
    from tools.pilot_state import read_effective_canonical, terminal_reviewer_evidence

    run_root, attempt_id, _document, selected = _reviewed(tmp_path, flag=True)
    assert selected.status == "EFFECTIVE_SELECTED"
    assert read_effective_canonical(run_root, attempt_id)["document_digest"]
    reviewer = terminal_reviewer_evidence(run_root, attempt_id)
    assert reviewer["isolation"] == "independence_unverified" and reviewer["self_accepted"] is True


def test_with_the_flag_an_isolated_review_is_not_marked(tmp_path: Path) -> None:
    from tools.pilot_state import terminal_reviewer_evidence

    run_root, attempt_id, _document, _selected = _reviewed(tmp_path, flag=True, fresh_context=True)
    reviewer = terminal_reviewer_evidence(run_root, attempt_id)
    assert reviewer["isolation"] == "verified" and "self_accepted" not in reviewer


# ------------------------------------------------------------------ 3: effective canonical receipt (ledger)


def test_without_the_flag_an_unisolated_ledger_cannot_bind_an_effective_canonical(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _effective_bundle, _reviewer_protocol_inputs
    from tools.pilot_state import publish_effective_canonical, publish_reviewer_session_ledger

    for name, flag in (("off", False), ("on", True)):
        run_root, attempt_id = _run(tmp_path / name, flag=flag)
        document = automated_document()
        session, _package, _review, _receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, fresh_context=False)
        ledger = publish_reviewer_session_ledger(run_root, attempt_id, session)["digest"]
        if flag:
            publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), ledger)
        else:
            with pytest.raises(ValueError, match="invalid effective canonical selection") as error:
                publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), ledger)
            assert "invalid effective canonical receipt" in str(error.value.__cause__)


# ------------------------------------------------------------------ 4-5: automation review boundary and acceptance


def test_without_the_flag_an_unisolated_automation_boundary_is_refused(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _effective_bundle, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical

    run_root, attempt_id = _run(tmp_path, flag=False)
    document = automated_document()
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    with pytest.raises(ValueError, match="invalid automation review boundary"):
        _automation_boundary(run_root, attempt_id, document, fresh_context=False)


def test_with_the_flag_an_unisolated_automation_review_is_accepted(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import review
    from tools.automation_validation import validate_accepted_autotest_review

    run_root, attempt_id, document, _selected = _reviewed(tmp_path, flag=True)
    artifact, boundary = _automation_boundary(run_root, attempt_id, document, fresh_context=False)
    reviewed = review(document, artifact, boundary, "ПРИНЯТО", session_id="auto-review")
    rows = validate_accepted_autotest_review(reviewed, artifact, document, host_isolation_receipt=boundary, run_root=run_root, attempt_id=attempt_id)
    assert "AUTOTEST_REVIEW_ISOLATION" not in {row["code"] for row in rows}, rows


def test_without_the_flag_an_unisolated_automation_review_keeps_the_isolation_diagnostic(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document, automation, review
    from tools.automation_validation import validate_accepted_autotest_review

    document = automated_document()
    artifact = automation(document)
    boundary = {"host_isolation": {"fresh_context": False, "distinct_invocations": True}}
    reviewed = review(document, artifact, {"generator_invocation_id": "g", "reviewer_invocation_id": "r"}, "ПРИНЯТО", session_id="s")
    rows = validate_accepted_autotest_review(reviewed, artifact, document, host_isolation_receipt=boundary)
    assert "AUTOTEST_REVIEW_ISOLATION" in {row["code"] for row in rows}


# ------------------------------------------------------------------ 6: finalization


@pytest.mark.parametrize("self_accepted", [False, True])
def test_finalization_requires_an_effective_canonical_only_for_an_admitted_review(self_accepted: bool) -> None:
    from tools.finalize_attempt import effective_canonical_required

    reviewer = {"session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1,
                "pre_verdict_abort": False, "isolation": "independence_unverified"}
    if self_accepted:
        reviewer["self_accepted"] = True
    assert effective_canonical_required(reviewer) is self_accepted
    assert effective_canonical_required({**reviewer, "isolation": "verified"}) is True


# ------------------------------------------------------------------ 8: terminal result


def test_a_self_accepted_local_pass_is_accepted_and_marked() -> None:
    from tools.pilot_state import _validate_result_record, terminal_result

    facts = _local(reviewer_isolation_state="independence_unverified")
    assert terminal_result(facts, "local-pilot-v1")["accepted"] is False
    assert "review_independence" not in terminal_result(facts, "local-pilot-v1")
    marked = terminal_result(facts | {"review_independence": "SELF"}, "local-pilot-v1")
    assert marked["accepted"] is True and marked["review_independence"] == "SELF"
    assert _validate_result_record(marked) is True
    failed = terminal_result(facts | {"review_independence": "SELF", "verification": "FAIL", "exact_target_pass": False}, "local-pilot-v1")
    assert failed["accepted"] is False and failed["review_independence"] == "SELF"


def test_a_verified_review_cannot_be_marked_self() -> None:
    from tools.pilot_state import terminal_result

    with pytest.raises(ValueError):
        terminal_result(_local() | {"review_independence": "SELF"}, "local-pilot-v1")


# ------------------------------------------------------------------ 7: compatibility


def test_compatibility_names_a_self_accepted_review() -> None:
    from tools.compatibility_contract import reviewer_independence

    assert reviewer_independence({"isolation": "verified"}) == "verified"
    assert reviewer_independence({"isolation": "independence_unverified"}) == "independence_unverified"
    assert reviewer_independence({"isolation": "independence_unverified", "self_accepted": True}) == "self_accepted"


# ------------------------------------------------------------------ finish without the flag (4a gap)


def _manual_unverified(run_root: Path, attempt_id: str, profile: str) -> dict:
    from tools import pilot_state
    from tools.finalize_attempt import finalize_attempt

    reviewer = pilot_state.terminal_reviewer_evidence(run_root, attempt_id)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    facts = {
        "run_id": run_id, "attempt_id": attempt_id, "attempt_state": "TERMINAL", "policy_profile": profile,
        "canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True,
        "reviewer_session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1,
        "reviewer_pre_verdict_abort": False, "reviewer_isolation_state": reviewer["isolation"],
        "trace_valid": True, "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "NOT_APPLICABLE", "execution_applicability": "NOT_APPLICABLE",
        "automation_accepted": False, "generated_required_count": 0, "generated_materialized_count": 0,
        "generated_retained_count": 0, "exact_target_pass": False, "operational_reliable": True,
        "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": None, "reason_code": "INDEPENDENCE_UNVERIFIED",
        "blocker_count": 0, "mixed_manual_traceable": False, "prior_stage_cause": "INDEPENDENCE_UNVERIFIED",
    }
    branch = {"attempt_id": attempt_id, "evidence": {}, "stage_causes": ["INDEPENDENCE_UNVERIFIED"]}
    return dict(finalize_attempt(branch, None, project=run_root, verification="NOT_APPLICABLE", facts=facts, run_root=run_root)["result"])


def _refused_selection(tmp_path: Path, profile: str) -> tuple[Path, str]:
    from tools.orchestrate_test_case_revision import OrchestrationError

    run_root, attempt_id = _run(tmp_path, profile, flag=False)
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tools.orchestrate_test_case_revision import orchestrate_revision

    document = automated_document()
    bundle_root = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=bundle_root, fresh_context=False)
    with pytest.raises(OrchestrationError):
        orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    return run_root, attempt_id


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_finish_closes_an_unisolated_review_without_the_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str], profile: str) -> None:
    from tools.run_pipeline import cmd_finish

    root, attempt_id = _refused_selection(tmp_path / "manual", profile)
    manual = _manual_unverified(root, attempt_id, profile)
    root, attempt_id = _refused_selection(tmp_path / "finish", profile)
    code = cmd_finish(SimpleNamespace(project=str(tmp_path / "finish"), run=root.name))
    output = json.loads(capsys.readouterr().out)
    axes = ("completion", "verification", "coverage", "accepted", "reason_code")
    assert {key: output.get(key) for key in axes} == {key: manual.get(key) for key in axes}, output
    assert output["reason_code"] == "INDEPENDENCE_UNVERIFIED" and output["accepted"] is False and code != 0
    assert "--accept-self-review" in output["fix"]


def test_ready_hints_at_the_choice_without_stopping(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools.ready import check

    report = check(_python_project(tmp_path, runtime=False), [], "cases-only-v1", "nested")
    row = next(item for item in report["checks"] if item["id"] == "review:isolation")
    assert row["level"] == "OK" and "--accept-self-review" in row["fix"]


def test_ready_hint_repeats_the_request_arguments(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools.ready import check

    report = check(_python_project(tmp_path, runtime=False), ["docs/task.md"], "cases-only-v1", "nested")
    fix = next(item for item in report["checks"] if item["id"] == "review:isolation")["fix"]
    assert '--docs "docs/task.md"' in fix and '--module "nested"' in fix and "--profile cases-only-v1" in fix
    assert fix.endswith("--accept-self-review")
