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


def _generated_run(tmp_path: Path) -> dict:
    """A run with the context, the marker and two generator answers published (as test_reviewer_protocol does)."""
    from tests.test_batch_assembly import _fragment, _header, _plan
    from tests.test_reviewer_protocol import _new_run, _session
    from tools.pilot_state import derive_state, publish_context_selection, publish_model_request, publish_model_stage_artifact
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "evidence.py").write_text("value = 1\n", encoding="utf-8")
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
    tmp = run_root / "tmp"
    return {
        "run_root": run_root, "attempt_id": attempt_id, "project": project, "tmp": tmp, "fragment_digests": [row["digest"] for row in fragments],
        "header": _put(tmp / "header.json", header), "plan": _put(tmp / "plan.json", plan),
        "fragments": [_put(tmp / f"fragment-{index}.json", value) for index, value in enumerate(fragments)],
        "inventory": _put(tmp / "inventory.json", inventory), "context": _put(tmp / "context.json", context),
        "marker": _put(tmp / "marker.json", marker), "start": _put(tmp / "session-start.json", start),
    }


def _open_session(run: dict) -> tuple[str, dict]:
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
    code, opened = _cmd("tools.orchestrate_test_case_revision", "open-session", "--candidate", str(tmp / "candidate.json"),
                        "--candidate-receipt", receipt, "--assembly-receipt", str(tmp / "assembly-receipt.json"),
                        "--inventory-receipt", run["inventory"], "--context-receipt", run["context"],
                        "--context-marker-output", run["marker"], *generator_flags, "--session-start", run["start"],
                        "--run-root", run_root, "--attempt-id", attempt)
    assert code == 0 and opened["status"] == "opened", opened
    code, digest = _api("document-digest", "--document", str(tmp / "candidate.json"))
    assert code == 0 and digest["result"] == published["candidate_bundle_receipt"]["document_sha256"], digest
    return digest["result"], opened["reviewer_session_boundary"]


def _reviewer_request(run: dict, boundary: dict) -> tuple[int, dict]:
    return _api("model-request", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "tc-reviewer:canonical",
                "--model-id", "model-reviewer", "--invocation-id", boundary["reviewer_invocation_id"],
                "--input-digest", boundary["canonical_branch_digest"], "--input-digest", boundary["package_digest"])


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

