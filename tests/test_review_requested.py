"""FaT-10b: REVIEW_REQUESTED by command. The chain from assembly to the reviewer model request runs only through
`python -m tools.…` commands; the fixture only prepares the generator answers a real run already has."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    return (run_root / "events.jsonl").read_bytes()


def _generated_run(tmp_path: Path, self_review: bool = False) -> dict:
    """A run with the context, the marker and two generator answers published (as test_reviewer_protocol does)."""
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
        from tools.run_pipeline import run_phase_one_spine

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
    header = _header()
    plan = _plan(header, context)
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
    for index, fragment in enumerate(fragments, start=1):
        stage = f"tc-generator:{fragment['batch_id']}"
        publish_model_request(run_root, attempt_id, stage, model_id="model-generator", invocation_id=f"invoke-{index}",
                              input_digests=[marker_digest, context["digest"], fragment["plan_digest"], fragment["header_digest"]])
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
        "marker": _put(tmp / "marker.json", marker), "start": _put(tmp / "session-start.json", start),
    }


def _open_session(run: dict, start: str = "fixture", budget_delta: int = 0, fresh: str = "yes") -> tuple[str, dict]:
    """assemble → publish r1 → open-session, by commands only; returns the candidate digest and the boundary."""
    tmp, run_root, attempt = run["tmp"], str(run["run_root"]), run["attempt_id"]
    fragment_flags = [flag for path in run["fragments"] for flag in ("--fragment", path)]
    code, assembled = _api("assemble", "--header", run["header"], "--plan", run["plan"], *fragment_flags,
                           "--out-candidate", str(tmp / "candidate.json"), "--out-receipt", str(tmp / "assembly-receipt.json"))
    assert code == 0, assembled
    code, published = _cmd("tools.orchestrate_test_case_revision", "publish", "--candidate", str(tmp / "candidate.json"),
                           "--output-dir", str(run["project"] / "bundles"), "--run-root", run_root, "--attempt-id", attempt)
    assert code == 0 and published["status"] == "published", published
    receipt = _put(tmp / "candidate-receipt.json", published["candidate_bundle_receipt"])
    generator_flags = [flag for path in run["fragments"] for flag in ("--generator-fragment", path)]
    package_flags = ["--candidate", str(tmp / "candidate.json"), "--candidate-receipt", receipt, "--assembly-receipt", str(tmp / "assembly-receipt.json"),
                     "--inventory-receipt", run["inventory"], "--context-receipt", run["context"], "--context-marker-output", run["marker"], *generator_flags]
    before = _journal(run["run_root"])
    if start == "command":  # FaT-10d: the session start from tools.api session-start, not by hand
        code, built = _api("session-start", "--run-root", run_root, "--attempt", attempt, *package_flags, "--fresh-context", fresh,
                           "--reviewer-invocation-id", "reviewer-subagent-1", "--out", str(tmp / "session-start.json"))
        assert code == 0, built
        assert _journal(run["run_root"]) == before  # it writes nothing in the run
        run["start"] = built["out"]
        if budget_delta:
            value = json.loads(Path(run["start"]).read_bytes())
            value["context_budget_bytes"] += budget_delta
            _put(Path(run["start"]), value)
    code, opened = _cmd("tools.orchestrate_test_case_revision", "open-session", *package_flags, "--session-start", run["start"],
                        "--run-root", run_root, "--attempt-id", attempt)
    run["opened"] = opened
    if budget_delta < -_RESERVE:  # the refusal case: the caller checks it
        assert code == 2 and _journal(run["run_root"]) == before, opened
        return "", opened
    assert code == 0 and opened["status"] == "opened", opened
    code, digest = _api("document-digest", "--document", str(tmp / "candidate.json"))
    assert code == 0 and digest["result"] == published["candidate_bundle_receipt"]["document_sha256"], digest
    return digest["result"], opened["reviewer_session_boundary"]


def _reviewer_request(run: dict, boundary: dict) -> tuple[int, dict]:
    return _api("model-request", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "tc-reviewer:canonical",
                "--model-id", "model-reviewer", "--invocation-id", boundary["reviewer_invocation_id"],
                "--input-digest", boundary["canonical_branch_digest"], "--input-digest", boundary["package_digest"])


_RESERVE = 256 * 1024  # .skillsrc limits.context_batch_bytes by default


def _review(run: dict, digest: str, stage: str = "tc-reviewer:canonical") -> tuple[int, dict]:
    return _api("review-requested", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", stage, "--digest", digest)


def test_open_session_review_requested_and_the_reviewer_request_by_commands_only(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    candidate, boundary = _open_session(run)
    assert boundary["canonical_branch_digest"] == candidate
    # the blocker of the Petclinic run: without the event the request refuses
    before = _journal(run["run_root"])
    code, refused = _reviewer_request(run, boundary)
    assert code == 2 and "must bind its prior candidate and reviewer boundary" in refused["message"], refused
    assert _journal(run["run_root"]) == before
    code, recorded = _review(run, candidate)
    assert code == 0 and recorded["status"] == "ok", recorded
    event = recorded["result"]
    assert (event["event_type"], event["stage_instance_id"], event["artifact_digest"], event["actor"]) == (
        "REVIEW_REQUESTED", "tc-reviewer:canonical", candidate, "controller"), event
    code, requested = _reviewer_request(run, boundary)
    assert code == 0 and requested["status"] == "ok", requested
    rows = [json.loads(line) for line in _journal(run["run_root"]).decode("utf-8").splitlines()]
    order = [row["event_type"] for row in rows if row.get("stage_instance_id") in {"assembly", "tc-reviewer:canonical"}]
    assert order == ["CANDIDATE_PUBLISHED", "REVIEW_REQUESTED", "MODEL_REQUESTED"], order


def test_the_same_digest_again_is_a_no_op_and_another_is_refused(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    candidate, _boundary = _open_session(run)
    code, first = _review(run, candidate)
    assert code == 0, first
    before = _journal(run["run_root"])
    code, again = _review(run, candidate)
    assert code == 0 and again["result"] == first["result"] and _journal(run["run_root"]) == before, again
    code, other = _review(run, "sha256:" + "7" * 64)
    assert code == 2 and other["message"].startswith("REVIEW_ALREADY_REQUESTED"), other
    assert _journal(run["run_root"]) == before


@pytest.mark.parametrize("stage", ["tc-generator:BATCH-fixture", "tc-to-autotest:r1", "autotest-reviewer:r3", "tc-reviewer:other", "assembly"])
def test_only_reviewer_stages_take_a_review_request(tmp_path: Path, stage: str) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _review(run, run["fragment_digests"][0], stage)
    assert code == 2 and refused["message"].startswith("REVIEW_STAGE_INVALID"), refused
    assert _journal(run["run_root"]) == before


def test_the_digest_must_be_the_published_candidate_of_the_reviewed_stage(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    # a generator fragment is a published candidate, but not the assembled one the canonical reviewer reads
    for digest in (run["fragment_digests"][0], "sha256:" + "7" * 64):
        code, refused = _review(run, digest)
        assert code == 2 and refused["message"].startswith("REVIEW_CANDIDATE_NOT_PUBLISHED"), refused
    code, refused = _review(run, run["fragment_digests"][0], "autotest-reviewer:r1")
    assert code == 2 and refused["message"].startswith("REVIEW_CANDIDATE_NOT_PUBLISHED") and "tc-to-autotest:r1" in refused["message"], refused
    code, refused = _review(run, "sha256:short")
    assert code == 2 and refused["message"].startswith("REVIEW_DIGEST_INVALID"), refused
    assert _journal(run["run_root"]) == before


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
    code, digest = _api("document-digest", "--document", str(run["tmp"] / "candidate.json"))
    assert code == 0, digest
    selected = {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": digest["result"]}
    rejected = verdict == "ТРЕБУЕТ ДОРАБОТКИ"
    report = {
        "verdict": verdict, "candidate": selected, "effective": None if rejected else selected,
        "reviewed_case_ids": [case["case_id"] for case in candidate["test_cases"]], "corrections": [],
        "findings": [{"severity": "BLOCKING", "code": "REVIEW_REJECTED", "message": "Нужна доработка.", "evidence": ["REQ"],
                      "related_ids": [candidate["test_cases"][0]["case_id"]]}] if rejected else [],
    }
    return _put(run["tmp"] / "review.json", {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report}, "warnings": []})


def _reviewed(tmp_path: Path, verdict: str = "ПРИНЯТО", evidence: tuple[str, ...] = (), start: str = "fixture") -> tuple[dict, str]:
    run = _generated_run(tmp_path)
    candidate, boundary = _open_session(run, start)
    assert _review(run, candidate)[0] == 0
    code, requested = _reviewer_request(run, boundary)
    assert code == 0, requested
    run["evidence"] = []
    for path in evidence:
        code, given = _evidence(run, path)
        assert code == 0, given
        run["evidence"].append(given["result"]["evidence_receipt"]["digest"])
    review = _review_answer(run, verdict)
    code, answered = _api("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"],
                          "--stage", "tc-reviewer:canonical", "--artifact", review)
    assert code == 0, answered
    return run, review


def _evidence(run: dict, *paths: str) -> tuple[int, dict]:
    return _api("review-evidence", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], *[flag for path in paths for flag in ("--path", path)])


def _complete(run: dict) -> tuple[int, dict]:
    return _api("complete-review", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--out", str(run["tmp"] / "session.json"))


def _select(run: dict, review: str | None) -> tuple[int, dict]:
    tmp = run["tmp"]
    evidence = []
    for index, digest in enumerate(run.get("evidence", [])):
        code, read = _api("read-context", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--digest", digest,
                          "--out", str(tmp / f"evidence-{index}.json"))
        assert code == 0, read
        evidence += ["--evidence-receipt", read["out"]]
    return _cmd("tools.orchestrate_test_case_revision", "select", "--candidate", str(tmp / "candidate.json"),
                "--candidate-receipt", str(tmp / "candidate-receipt.json"), "--assembly-receipt", str(tmp / "assembly-receipt.json"),
                "--inventory-receipt", run["inventory"], "--context-receipt", run["context"], "--context-marker-output", run["marker"],
                *[flag for path in run["fragments"] for flag in ("--generator-fragment", path)],
                "--reviewer-session", str(tmp / "session.json"), *(["--review", review] if review else []), *evidence,
                "--run-root", str(run["run_root"]), "--attempt-id", run["attempt_id"], "--output-dir", str(run["project"] / "bundles"))


def test_from_open_session_to_select_by_commands_only(tmp_path: Path) -> None:
    run, review = _reviewed(tmp_path)
    code, completed = _complete(run)
    assert code == 0 and completed["status"] == "ok", completed
    session = json.loads((run["tmp"] / "session.json").read_bytes())
    assert session["status"] == "COMPLETED" and [event["event_type"] for event in session["events"]] == [
        "REVIEW_SESSION_STARTED", "AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED"], session
    code, selected = _select(run, review)
    assert code == 0 and selected["status"] != "error" and selected["effective_bundle_receipt"] is not None, selected
    code, effective = _api("read-effective", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    assert code == 0, effective
    # the same command after select gives the same completed session
    code, again = _api("complete-review", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    assert code == 0 and again["result"] == session, again


def test_a_rejection_is_completed_the_same_way(tmp_path: Path) -> None:
    run, review = _reviewed(tmp_path, "ТРЕБУЕТ ДОРАБОТКИ")
    code, completed = _complete(run)
    assert code == 0, completed
    verdict = json.loads((run["tmp"] / "session.json").read_bytes())["events"][1]
    assert verdict["verdict"] == "REJECTED", verdict
    code, selected = _select(run, review)
    assert code == 0 and selected["effective_bundle_receipt"] is None, selected


def test_no_reviewer_answer_yet_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _open_session(run)
    before = _journal(run["run_root"])
    code, refused = _complete(run)
    assert code == 2 and refused["message"].startswith("REVIEWER_RESPONSE_MISSING"), refused
    assert _journal(run["run_root"]) == before and not (run["tmp"] / "session.json").exists()


def test_evidence_pair_then_complete_review_then_select_by_commands_only(tmp_path: Path) -> None:
    run, review = _reviewed(tmp_path, evidence=("src/extra.py",))
    code, again = _evidence(run, "src/extra.py")
    assert code == 2 and again["message"].startswith("REVIEWER_EVIDENCE_DUPLICATE"), again
    code, unknown = _evidence(run, "src/missing.py")
    assert code == 2 and unknown["message"].startswith("REVIEWER_EVIDENCE_PATH_UNKNOWN"), unknown
    code, completed = _complete(run)
    assert code == 0, completed
    session = json.loads((run["tmp"] / "session.json").read_bytes())
    assert [event["event_type"] for event in session["events"]] == [
        "REVIEW_SESSION_STARTED", "EVIDENCE_REQUESTED", "EVIDENCE_PROVIDED", "AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED"], session
    assert session["events"][2]["provided_digest"] == run["evidence"][0]
    code, selected = _select(run, review)
    assert code == 0 and selected["effective_bundle_receipt"] is not None, selected


def test_evidence_beyond_the_budget_is_refused_as_context_limit(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _open_session(run, "command", -_RESERVE)  # the budget is exactly the package: no room for evidence
    code, refused = _evidence(run, "src/extra.py")
    assert code == 2 and refused["message"].startswith("REVIEW_CONTEXT_LIMIT"), refused
    # the abort closes the branch as before FaT-10d
    code, aborted = _api("review-abort", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--reason", "REVIEW_CONTEXT_LIMIT",
                         "--out", str(run["tmp"] / "session.json"))
    assert code == 0, aborted
    code, selected = _select(run, None)
    assert code == 0 and selected["effective_bundle_receipt"] is None, selected
    assert selected["facts"]["attempt_state"] == "TERMINAL" and selected["facts"]["reason_code"] == "REVIEW_CONTEXT_LIMIT", selected


def test_abort_then_select_closes_the_branch_without_a_verdict(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    candidate, _boundary = _open_session(run)
    assert _review(run, candidate)[0] == 0
    code, refused = _api("review-abort", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--reason", "MODEL_TIRED")
    assert code == 2 and refused["message"].startswith("REVIEWER_ABORT_REASON_INVALID"), refused
    code, aborted = _api("review-abort", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--reason", "REVIEW_CONTEXT_LIMIT",
                         "--out", str(run["tmp"] / "session.json"))
    assert code == 0, aborted
    session = json.loads((run["tmp"] / "session.json").read_bytes())
    assert session["status"] == "ABORTED" and session["events"][-1] == {"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REVIEW_CONTEXT_LIMIT"}
    code, selected = _select(run, None)
    assert code == 0 and selected["effective_bundle_receipt"] is None, selected
    assert selected["facts"]["attempt_state"] == "TERMINAL" and selected["facts"]["reason_code"] == "REVIEW_CONTEXT_LIMIT", selected
    assert selected["facts"]["accepted"] is False, selected
    # the session is closed now: no more evidence
    code, closed = _evidence(run, "src/extra.py")
    assert code == 2 and closed["message"].startswith("REVIEWER_SESSION_CLOSED"), closed


# --------------------------------------------------------------------------------------
# FaT-10d: the session start from a command; a budget below the package is refused at open-session
# --------------------------------------------------------------------------------------

def test_session_start_by_command_then_review_and_select(tmp_path: Path) -> None:
    from tools.pilot_state import self_review_accepted

    run, review = _reviewed(tmp_path, evidence=("src/extra.py",), start="command")
    start = json.loads(Path(run["start"]).read_bytes())
    package = run["opened"]["package_binding"]
    assert start["context_budget_bytes"] == package["base_package_byte_count"] + _RESERVE, start
    assert start["generator_invocation_id"] == "invoke-1" and start["reviewer_invocation_id"] == "reviewer-subagent-1", start
    assert start["host_isolation"]["fresh_context"] is True and not self_review_accepted(run["run_root"]), start
    assert start["host_isolation"]["distinct_invocations"] is True, start
    assert run["opened"]["reviewer_session_boundary"]["context_budget_bytes"] == start["context_budget_bytes"]
    code, completed = _complete(run)
    assert code == 0, completed
    code, selected = _select(run, review)
    assert code == 0 and selected["effective_bundle_receipt"] is not None, selected


def test_session_start_reserve_is_the_projects_context_batch_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import orchestrate_test_case_revision as revision, run_pipeline

    run = _generated_run(tmp_path)
    _open_session(run)  # publishes the candidate and the receipts the start is built from
    asked = []
    monkeypatch.setattr(run_pipeline, "_scan_context_budget", lambda _inventory, project: asked.append(project) or 4096)
    tmp = run["tmp"]
    load = lambda path: json.loads(Path(path).read_bytes())  # noqa: E731
    start = revision.reviewer_session_start(
        run["run_root"], run["attempt_id"], candidate=load(tmp / "candidate.json"), candidate_receipt=tmp / "candidate-receipt.json",
        assembly_receipt=load(tmp / "assembly-receipt.json"), inventory_receipt=load(run["inventory"]), context_receipts=[load(run["context"])],
        context_marker_output=load(run["marker"]), generator_fragments=[load(path) for path in run["fragments"]],
        fresh_context=True, reviewer_invocation_id="reviewer-subagent-1",
    )
    assert asked == [run["project"].resolve()], asked  # .skillsrc limits.context_batch_bytes of this project
    assert start["context_budget_bytes"] == run["opened"]["package_binding"]["base_package_byte_count"] + 4096, start


def test_a_budget_below_the_package_is_refused_at_open_session(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _candidate, opened = _open_session(run, "command", -_RESERVE - 1)
    assert opened["status"] == "error" and opened["reason_code"] == "REVIEW_CONTEXT_LIMIT", opened
    assert "smaller than the review package" in opened["message"] and "tools.api session-start" in opened["fix"], opened


def test_select_names_the_inner_refusal_and_its_fix(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from tools import orchestrate_test_case_revision as revision

    def refuse(*_args, **_kwargs):
        raise ValueError("REVIEW_CONTEXT_LIMIT")

    monkeypatch.setattr(revision, "load_json_strict", lambda _path: {})
    monkeypatch.setattr(revision, "_load_receipt", lambda _path: None)
    monkeypatch.setattr(revision, "_read_existing_context_receipts", lambda *_args: [])
    monkeypatch.setattr(revision, "reviewer_package_binding", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(revision, "orchestrate_revision", refuse)
    flags = ["--candidate", "c", "--candidate-receipt", "r", "--assembly-receipt", "a", "--inventory-receipt", "i", "--context-receipt", "x",
             "--context-marker-output", "m", "--generator-fragment", "g", "--reviewer-session", "s", "--run-root", str(tmp_path),
             "--attempt-id", "a1", "--output-dir", str(tmp_path / "out")]
    assert revision.main(["select", *flags]) == 2
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["code"] == "ORCHESTRATION_INPUT" and printed["reason_code"] == "REVIEW_CONTEXT_LIMIT", printed
    assert "review-abort" in printed["fix"] and "session-start" in printed["fix"], printed


def test_automation_boundary_facts_from_the_journal_by_commands_only(tmp_path: Path) -> None:
    """FaT-10d: read-effective → model-response tc-to-autotest:r1 → automation-boundary-facts → open-automation-boundary
    → review-requested → model-request autotest-reviewer:r1; the agent computes and copies nothing."""
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automated_document, automation
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical
    from tools.run_pipeline import run_phase_one_spine

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


def test_fresh_context_is_the_hosts_statement_in_both_self_profiles(tmp_path: Path) -> None:
    """FaT-10d: --fresh-context yes|no is an input; "no" without SELF is refused, both values pass with SELF and change the digest."""
    run = _generated_run(tmp_path / "plain")
    _open_session(run)  # publishes the files the start is built from (the fixture start opens the session)
    flags = ["--candidate", str(run["tmp"] / "candidate.json"), "--candidate-receipt", str(run["tmp"] / "candidate-receipt.json"),
             "--assembly-receipt", str(run["tmp"] / "assembly-receipt.json"), "--inventory-receipt", run["inventory"],
             "--context-receipt", run["context"], "--context-marker-output", run["marker"],
             *[flag for path in run["fragments"] for flag in ("--generator-fragment", path)]]

    def start(target: dict, fresh: str, reviewer: str = "reviewer-subagent-1") -> tuple[int, dict]:
        return _api("session-start", "--run-root", str(target["run_root"]), "--attempt", target["attempt_id"], *flags,
                    "--fresh-context", fresh, "--reviewer-invocation-id", reviewer)

    code, refused = start(run, "no")
    assert code == 2 and refused["code"] == "INDEPENDENCE_UNVERIFIED" and "--accept-self-review" in refused["message"], refused
    code, fresh = start(run, "yes")
    assert code == 0 and fresh["result"]["host_isolation"]["fresh_context"] is True, fresh
    code, bad = start(run, "maybe")
    assert code != 0, bad
    # the same generator id is stated: distinct_invocations is computed false, not trusted
    code, same = start(run, "yes", "invoke-1")
    assert code == 0 and same["result"]["host_isolation"]["distinct_invocations"] is False, same

    self_run = _generated_run(tmp_path / "self", self_review=True)
    _open_session(self_run)
    flags = ["--candidate", str(self_run["tmp"] / "candidate.json"), "--candidate-receipt", str(self_run["tmp"] / "candidate-receipt.json"),
             "--assembly-receipt", str(self_run["tmp"] / "assembly-receipt.json"), "--inventory-receipt", self_run["inventory"],
             "--context-receipt", self_run["context"], "--context-marker-output", self_run["marker"],
             *[flag for path in self_run["fragments"] for flag in ("--generator-fragment", path)]]
    code, no = start(self_run, "no")
    code_yes, yes = start(self_run, "yes")
    assert code == 0 and code_yes == 0, (no, yes)
    assert no["result"]["host_isolation"]["fresh_context"] is False and yes["result"]["host_isolation"]["fresh_context"] is True
    assert no["result"]["host_isolation"]["evidence_digest"] != yes["result"]["host_isolation"]["evidence_digest"], (no, yes)
