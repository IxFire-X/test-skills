"""FaT-10b: REVIEW_REQUESTED by command (the digest checks), the review of the automation by commands, the fixture of the runs with the generator answers
published, and the cycle of the review (``prepare`` / ``submit`` of ``tc-reviewer:canonical``): the session, the evidence and the budget."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_in_order, assert_refused_once

ROOT = Path(__file__).resolve().parents[1]


def _cmd(*args: str) -> tuple[int, dict]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])


def _api(command: str, *args: str) -> tuple[int, dict]:
    return _cmd("tools.api", command, *args)


def _put(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return str(path)


def _journal(run_root: Path) -> bytes:
    """J1: the journal WITHOUT its COMMAND_REFUSED events (every refusal adds one); the value remembers how many there were, so a test
    that compares «before / after a refusal» states the one event the refusal added with ``assert_refused_once``."""
    from tests.journal_helpers import journal_minus_refusals

    return journal_minus_refusals(run_root)


def _generated_run(tmp_path: Path, self_review: bool = False, generated: bool = True, header: dict | None = None,
                   requirements: list | None = None) -> dict:
    """A run with the context, the marker and two generator answers published (as test_reviewer_protocol does);
    *header*, *requirements* — another canonical header and source requirements (PG1: 1.1.0 for param_group, no Russian)."""
    from tests.test_batch_assembly import _fragment, _header, _plan
    from tests.test_reviewer_protocol import _new_run, _session
    from tools.pilot_state import derive_state, publish_context_selection, publish_model_request, publish_model_stage_artifact
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "evidence.py").write_text("value = 1\n", encoding="utf-8")
    (project / "src" / "extra.py").write_text("extra = 2\n", encoding="utf-8")  # FaT-10c: evidence the reviewer may ask for
    if self_review:  # FaT-10d: a run started with scan --accept-self-review (SELF admitted)
        from tests.helpers import build_phase_two_baseline
        from tests.spine_helpers import run_phase_one_spine

        identity = {"project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1"}
        baseline_value = build_phase_two_baseline(project, identity, skill_pack_root=project / ".pilot-runs")
        started = run_phase_one_spine(project, "cases-only-v1", {"request_id": "review-1", "execution_requested": False, "accept_self_review": True},
                                      identity, baseline_value, {})
        run_root, attempt_id = Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]
    else:
        run_root, attempt_id = _new_run(project)
    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    selected = select_context_batches(inventory, project, [next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "src/evidence.py")], byte_budget=4096)[0]["receipt"]
    context = publish_context_selection(run_root, attempt_id, selected)
    header = _header() if header is None else header
    plan = _plan(header, context, requirements)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    marker = {
        "schema_version": "5.0.0", "stage": "context-marker",
        "artifacts": {"analytics_documentation": {"requirements": []}, "source_code_and_diff": {"sources": ["src/evidence.py — immutable test source"]}},
        "warnings": [],
    }
    from tools.batch_assembly import assemble_candidate

    marker["artifacts"]["analytics_documentation"]["requirements"] = assemble_candidate(header, plan, fragments)[0]["source_requirements"]
    publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="model-context", invocation_id=f"context-{attempt_id}",
                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], context["digest"]])
    marker_digest = publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", marker)["content_digest"]
    for index, fragment in enumerate(fragments if generated else [], start=1):  # FaT-DG: generated=False stops before the generator
        if index == 1:
            from tools.pilot_state import publish_generator_input

            publish_generator_input(run_root, attempt_id, "header", header)
            publish_generator_input(run_root, attempt_id, "plan", plan)
        stage = f"tc-generator:{fragment['batch_id']}"
        prior = []
        if index > 1:  # FaT-BO: from batch 2 the capabilities of the accepted earlier batches are the fifth input
            from tools.pilot_state import publish_prior_capabilities

            prior = [publish_prior_capabilities(run_root, attempt_id, fragment["batch_id"])["digest"]]
        publish_model_request(run_root, attempt_id, stage, model_id="model-generator", invocation_id=f"invoke-{index}",
                              input_digests=[marker_digest, context["digest"], fragment["plan_digest"], fragment["header_digest"], *prior])
        publish_model_stage_artifact(run_root, attempt_id, stage, fragment)
    session, _package, _review = _session()
    start = {key: session[key] for key in ("session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes")}
    start["context_budget_bytes"] = 10_000_000  # the real package, not the 1000-byte fixture package
    tmp = run_root / "tmp"
    return {
        "run_root": run_root, "attempt_id": attempt_id, "project": project, "tmp": tmp, "fragment_digests": [row["digest"] for row in fragments],
        "header": _put(tmp / "header.json", header), "plan": _put(tmp / "plan.json", plan),
        "fragments": [_put(tmp / f"fragment-{index}.json", value) for index, value in enumerate(fragments)],
        "inventory": _put(tmp / "inventory.json", inventory), "context": _put(tmp / "context.json", context),
        "marker": _put(tmp / "marker.json", marker), "start": _put(tmp / "session-start.json", start), "marker_digest": marker_digest,
    }


def _digest_of(path: Path) -> str:
    from tools.canonical_document import document_sha256

    return document_sha256(json.loads(Path(path).read_bytes()))


def _review(run: dict, digest: str, stage: str = "tc-reviewer:canonical") -> tuple[int, dict]:
    return _api("review-requested", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", stage, "--digest", digest)


@pytest.mark.parametrize("stage", ["tc-generator:BATCH-fixture", "tc-to-autotest:r1", "autotest-reviewer:r3", "tc-reviewer:other", "assembly"])
def test_only_reviewer_stages_take_a_review_request(tmp_path: Path, stage: str) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _review(run, run["fragment_digests"][0], stage)
    assert code == 2 and refused["message"].startswith("REVIEW_STAGE_INVALID"), refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], "review-requested")  # J1: the one event the refusal itself added


def test_the_digest_must_be_the_published_candidate_of_the_reviewed_stage(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    # a generator fragment is a published candidate, but not the assembled one the canonical reviewer reads
    codes = []
    for digest in (run["fragment_digests"][0], "sha256:" + "7" * 64):
        code, refused = _review(run, digest)
        assert code == 2 and refused["message"].startswith("REVIEW_CANDIDATE_NOT_PUBLISHED"), refused
        codes.append(refused["code"])
    code, refused = _review(run, run["fragment_digests"][0], "autotest-reviewer:r1")  # a cases-only-v1 run: the automation is not its stage
    assert code == 2 and refused["message"].startswith("MODEL_STAGE_NOT_IN_PROFILE"), refused
    codes.append(refused["code"])
    code, refused = _review(run, "sha256:short")
    assert code == 2 and refused["message"].startswith("REVIEW_DIGEST_INVALID"), refused
    codes.append(refused["code"])
    assert _journal(run["run_root"]) == before
    assert_refused_in_order(run["run_root"], before, *(("review-requested", code) for code in codes))  # J1: the four refusals of the test


def test_the_automation_review_by_commands(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document, automation, durable_boundary, host_evidence
    from tests.test_generated_delta import _effective_bundle_digest

    document = automated_document()
    artifact = automation(document, 1, predecessor=None, correction_review=None, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    evidence = host_evidence("session-1")
    run_root, attempt_id, _boundary = durable_boundary(tmp_path, artifact, "session-1", evidence, generator=evidence["generator_invocation_id"],
                                                       reviewer=evidence["reviewer_invocation_id"], policy_profile="local-pilot-v1", document=document)
    run = {"run_root": run_root, "attempt_id": attempt_id}
    code, digest = _api("automation-digest", "--automation", _put(run_root / "tmp" / "automation.json", artifact))
    assert code == 0, digest
    code, receipt = _api("read-receipt", "--run-root", str(run_root), "--attempt", attempt_id,
                         "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, receipt
    request = ("model-request", "--run-root", str(run_root), "--attempt", attempt_id, "--stage", "autotest-reviewer:r1",
               "--model-id", "model-automation-reviewer", "--invocation-id", evidence["reviewer_invocation_id"],
               "--input-digest", digest["result"], "--input-digest", receipt["result"]["digest"])
    code, refused = _api(*request)
    assert code == 2, refused
    code, recorded = _review(run, digest["result"], "autotest-reviewer:r2")
    assert code == 2 and recorded["message"].startswith("REVIEW_CANDIDATE_NOT_PUBLISHED"), recorded
    code, recorded = _review(run, digest["result"], "autotest-reviewer:r1")
    assert code == 0 and recorded["result"]["artifact_digest"] == digest["result"], recorded
    code, requested = _api(*request)
    assert code == 0 and requested["status"] == "ok", requested


# --------------------------------------------------------------------------------------
# FaT-10c: the completed reviewer session for select comes from a command, not from hand-built JSON
# --------------------------------------------------------------------------------------

def _review_answer(run: dict, verdict: str = "ПРИНЯТО") -> str:
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    selected = {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": _digest_of(run["tmp"] / "candidate.json")}
    rejected = verdict == "ТРЕБУЕТ ДОРАБОТКИ"
    report = {
        "verdict": verdict, "candidate": selected, "effective": None if rejected else selected,
        "reviewed_case_ids": [case["case_id"] for case in candidate["test_cases"]], "corrections": [],
        "findings": [{"severity": "BLOCKING", "code": "REVIEW_REJECTED", "message": "Нужна доработка.", "evidence": ["REQ"],
                      "related_ids": [candidate["test_cases"][0]["case_id"]]}] if rejected else [],
    }
    return _put(run["tmp"] / "review.json", {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report}, "warnings": []})


def test_automation_boundary_facts_from_the_journal_by_commands_only(tmp_path: Path) -> None:
    """FaT-10d: read-effective → model-response tc-to-autotest:r1 → automation-boundary-facts → open-automation-boundary
    → review-requested → model-request autotest-reviewer:r1; the agent computes and copies nothing."""
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automated_document, automation
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical
    from tests.spine_helpers import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    started = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "auto-facts", "execution_requested": True}, identity, baseline, {})
    run_root, attempt_id = Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]
    document = automated_document()
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    run, tmp = ("--run-root", str(run_root), "--attempt", attempt_id), run_root / "tmp"

    code, effective = _api("read-effective", *run)
    assert code == 0, effective
    effective = effective["result"]
    statement = ("--fresh-context", "yes", "--reviewer-invocation-id", "automation-reviewer-subagent-1")
    code, refused = _api("automation-boundary-facts", *run, "--revision", "1", *statement)
    assert code == 2 and refused["message"].startswith("AUTOMATION_RESPONSE_MISSING"), refused
    code, requested = _api("model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                           "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])
    assert code == 0, requested
    artifact = automation(document, 1, predecessor=None, correction_review=None, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    code, answered = _api("model-response", *run, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))
    assert code == 0, answered
    before = _journal(run_root)
    code, built = _api("automation-boundary-facts", *run, "--revision", "1", *statement, "--out", str(tmp / "facts.json"))
    assert code == 0 and _journal(run_root) == before, built  # it writes nothing in the run
    facts = json.loads((tmp / "facts.json").read_bytes())
    code, digest = _api("automation-digest", "--automation", str(tmp / "automation.json"))
    assert code == 0 and facts["automation_digest"] == digest["result"], (facts, digest)
    assert (facts["effective_canonical_digest"], facts["effective_bundle_receipt_digest"]) == (
        effective["document_digest"], effective["effective_bundle_receipt_digest"]), facts
    assert facts["generator_invocation_id"] == "generator-1" and facts["reviewer_invocation_id"] == "automation-reviewer-subagent-1", facts
    assert facts["host_isolation"]["fresh_context"] is True and facts["host_isolation"]["distinct_invocations"] is True, facts
    # the statement is the host's: "no" in a run without SELF is refused with the SELF fix, nothing written
    code, refused = _api("automation-boundary-facts", *run, "--revision", "1", "--fresh-context", "no", "--reviewer-invocation-id", "x-1")
    assert code == 2 and refused["code"] == "INDEPENDENCE_UNVERIFIED" and "--accept-self-review" in refused["message"], refused
    assert _journal(run_root) == before
    code, opened = _api("open-automation-boundary", *run, "--facts", str(tmp / "facts.json"))
    assert code == 0, opened
    code, recorded = _review({"run_root": run_root, "attempt_id": attempt_id}, facts["automation_digest"], "autotest-reviewer:r1")
    assert code == 0, recorded
    code, boundary = _api("read-receipt", *run, "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, boundary
    code, reviewer = _api("model-request", *run, "--stage", "autotest-reviewer:r1", "--model-id", "model-automation-reviewer",
                          "--invocation-id", boundary["result"]["record"]["reviewer_invocation_id"],
                          "--input-digest", boundary["result"]["record"]["automation_digest"], "--input-digest", boundary["result"]["record"]["digest"])
    assert code == 0 and reviewer["status"] == "ok", reviewer


# --------------------------------------------------------------------------------------
# the review on the cycle of the agent: the session, the verdict, the evidence, the budget
# --------------------------------------------------------------------------------------

def _opened_reviewed(tmp_path: Path, verdict: str = "ПРИНЯТО") -> dict:
    from tests.test_review_two_commands import _open, _select_verdict, _verdict

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, selected = _select_verdict(run, _verdict(run, verdict))
    assert code == 0, selected
    return run


def test_prepare_and_submit_complete_the_session_and_read_effective_gives_the_set(tmp_path: Path) -> None:
    from tests.test_review_two_commands import _ledger

    run = _opened_reviewed(tmp_path)
    ledger = _ledger(run)
    assert ledger["status"] == "COMPLETED" and [event["event_type"] for event in ledger["events"]] == [
        "REVIEW_SESSION_STARTED", "AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED"], ledger
    code, effective = _api("read-effective", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective


def test_a_rejection_is_completed_the_same_way_and_has_no_effective_set(tmp_path: Path) -> None:
    from tests.test_review_two_commands import _ledger

    run = _opened_reviewed(tmp_path, "ТРЕБУЕТ ДОРАБОТКИ")
    assert _ledger(run)["events"][1]["verdict"] == "REJECTED"
    code, effective = _api("read-effective", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    assert code == 0 and effective["result"]["effective"] is None, effective


def test_evidence_beyond_the_budget_is_refused_as_context_limit_and_the_abort_closes_the_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cut_review import _evidence_verdict
    from tests.test_review_two_commands import _cycle, _open, _select_verdict
    from tools import run_pipeline

    run = _generated_run(tmp_path)
    monkeypatch.setattr(run_pipeline, "_scan_context_budget", lambda _inventory, project: 0)  # the budget is exactly the package: no room for evidence
    assert _open(run)[0] == 0
    code, refused = _select_verdict(run, _evidence_verdict(run, "src/extra.py"))
    assert code == 2 and refused["code"] == "REVIEW_CONTEXT_LIMIT", refused
    code, aborted = _cycle("submit", run, "--abort", "REVIEW_CONTEXT_LIMIT")
    assert code == 0 and aborted["result"]["outcome"] == "ABORTED" and aborted["result"]["status"] == "REVIEW_CONTEXT_LIMIT", aborted
    code, closed = _select_verdict(run, _evidence_verdict(run, "src/extra.py"))  # the session is closed now: no more evidence
    assert code == 2 and closed["code"] == "REVIEW_CLOSED", closed


def test_a_budget_below_the_package_is_refused_at_prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_two_commands import _open
    from tools import run_pipeline

    run = _generated_run(tmp_path)
    monkeypatch.setattr(run_pipeline, "_scan_context_budget", lambda _inventory, project: -1)
    before = _journal(run["run_root"])
    code, refused = _open(run)
    assert code == 2 and refused["code"] == "REVIEW_CONTEXT_LIMIT" and "smaller than the review package" in refused["message"] and refused["fix"], refused
    assert_refused_once(run["run_root"], before, refused["code"], "prepare")  # J1: the one event the refusal itself added


def test_the_reserve_of_the_budget_is_the_projects_context_batch_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_two_commands import _ledger, _open
    from tools import run_pipeline

    budgets = []
    for index, reserve in enumerate((4096, 8192)):
        run = _generated_run(tmp_path / str(index))
        asked = []
        monkeypatch.setattr(run_pipeline, "_scan_context_budget", lambda _inventory, project, asked=asked, reserve=reserve: asked.append(project) or reserve)
        assert _open(run)[0] == 0
        assert asked == [run["project"].resolve()], asked  # .skillsrc limits.context_batch_bytes of this project
        budgets.append(_ledger(run)["context_budget_bytes"])
    assert budgets[1] - budgets[0] == 4096, budgets
