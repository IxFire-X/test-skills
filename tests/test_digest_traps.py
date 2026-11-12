"""FaT-DG: the digest traps of the 28.10 campaign, each caught before anything is written, with the right value in the
refusal; the stage keeps its one retry. The chain runs through the commands of the agent (`prepare`, `submit`)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run, _journal, _put
from tests.helpers import prepare_stage, submit_stage
from tests.journal_helpers import assert_refused_once


def _publish_inputs(run: dict) -> tuple[str, str]:
    """The header and the plan of the fixture published the way ``submit --stage plan`` publishes what it built (the function that stayed)."""
    from tools import pilot_state

    header = json.loads(Path(run["header"]).read_bytes())
    header.pop("digest", None)
    published_header = pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "header", header)
    published_plan = pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "plan", json.loads(Path(run["plan"]).read_bytes()))
    return published_plan["digest"], published_header["digest"]


def test_one_digest_for_plan_and_header(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    plan = json.loads(Path(run["plan"]).read_bytes())
    assert plan_digest == plan["digest"] and header_digest == plan["header_digest"]
    # the same value again changes nothing
    before = _journal(run["run_root"])
    assert _publish_inputs(run) == (plan_digest, header_digest) and _journal(run["run_root"]) == before


def _uncovered(fragment: dict) -> dict:
    """The microservices 4d8491a2 answer: a canonical requirement of the batch that no case references."""
    from tools.batch_assembly import with_digest

    value = {key: item for key, item in fragment.items() if key != "digest"}
    extra = dict(value["requirements"][0], requirement_id=value["requirements"][0]["requirement_id"] + "-common", display_order=100)
    value["requirements"] = [*value["requirements"], extra]
    value["source_to_canonical_mappings"] = [dict(value["source_to_canonical_mappings"][0],
                                                  canonical_requirement_ids=[*value["source_to_canonical_mappings"][0]["canonical_requirement_ids"], extra["requirement_id"]])]
    return with_digest(value)


def test_microservices_4d8491a2_uncovered_requirement_before_the_write(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    _publish_inputs(run)
    fragment = json.loads(Path(run["fragments"][0]).read_bytes())
    stage = f"tc-generator:{fragment['batch_id']}"
    assert prepare_stage(run["run_root"], stage)[0] == 0
    bad = _uncovered(fragment)
    before = _journal(run["run_root"])
    code, refused = submit_stage(run["run_root"], stage, bad)
    assert code == 2 and refused["code"] == "BATCH_UNCOVERED_REQUIREMENT", refused
    assert bad["requirements"][1]["requirement_id"] in refused["message"] and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    # the one retry of the stage is still there
    code, answered = submit_stage(run["run_root"], stage, run["fragments"][0], "--transport-attempts", "2")
    assert code == 0, answered


def test_assemble_and_validate_fragment_agree_on_coverage(tmp_path: Path) -> None:
    import copy

    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.canonical_document import uncovered_requirements

    run = _generated_run(tmp_path, generated=False)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    plan, header, context = (json.loads(Path(run[name]).read_bytes()) for name in ("plan", "header", "context"))
    bad = _uncovered(fragments[0])
    with pytest.raises(BatchAssemblyError) as validated:
        validate_fragment(copy.deepcopy(bad), plan, header, context)
    with pytest.raises(BatchAssemblyError) as assembled:
        assemble_candidate(header, plan, [bad, fragments[1]])
    missing = bad["requirements"][1]["requirement_id"]
    assert validated.value.code == assembled.value.code == "BATCH_UNCOVERED_REQUIREMENT", (validated.value, assembled.value)
    assert missing in str(validated.value) and missing in str(assembled.value)
    # the same rule assemble applies to the whole document (SEMANTIC_UNCOVERED_REQUIREMENT)
    assert bad["requirements"][uncovered_requirements(bad["requirements"], fragments[0]["test_cases"])[0]]["requirement_id"] == missing
    validate_fragment(copy.deepcopy(fragments[0]), plan, header, context)
    assemble_candidate(header, plan, fragments)


def test_petclinic_rest_523ec561_reviewed_relations_digest_before_the_write(tmp_path: Path) -> None:
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automated_document, automation, review
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical
    from tests.spine_helpers import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    started = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "digest-traps", "execution_requested": True}, identity, baseline, {})
    run_root, attempt_id = Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]
    document = automated_document()
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    run, tmp = ("--run-root", str(run_root), "--attempt", attempt_id), run_root / "tmp"
    code, effective = _api("read-effective", *run)
    effective = effective["result"]
    # a wrong source digest in the generator request is named with the right one
    code, refused = _api("model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                         "--input-digest", effective["effective_bundle_receipt_digest"], "--input-digest", effective["document_digest"])
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and "tools.api read-effective" in refused["message"], refused
    assert _api("model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])[0] == 0
    artifact = automation(document, 1, predecessor=None, correction_review=None, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    assert _api("model-response", *run, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))[0] == 0
    statement = ("--fresh-context", "yes", "--reviewer-invocation-id", "automation-reviewer-subagent-1")
    assert _api("automation-boundary-facts", *run, "--revision", "1", *statement, "--out", str(tmp / "facts.json"))[0] == 0
    assert _api("open-automation-boundary", *run, "--facts", str(tmp / "facts.json"))[0] == 0
    facts = json.loads((tmp / "facts.json").read_bytes())
    assert _api("review-requested", *run, "--stage", "autotest-reviewer:r1", "--digest", facts["automation_digest"])[0] == 0
    code, boundary = _api("read-receipt", *run, "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")
    boundary = boundary["result"]["record"]
    assert _api("model-request", *run, "--stage", "autotest-reviewer:r1", "--model-id", "model-automation-reviewer",
                "--invocation-id", boundary["reviewer_invocation_id"], "--input-digest", boundary["automation_digest"], "--input-digest", boundary["digest"])[0] == 0
    good = review(document, artifact, boundary, "ПРИНЯТО", session_id=boundary["reviewer_session_id"])
    bad = json.loads(json.dumps(good))
    bad["artifacts"]["autotest_review"]["reviewed_relations_sha256"] = facts["automation_digest"]  # the digest of the artifact, not of relations
    before = _journal(run_root)
    response = ("model-response", *run, "--stage", "autotest-reviewer:r1")
    code, refused = _api(*response, "--artifact", _put(tmp / "review-bad.json", bad))
    assert code == 2 and refused["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH", refused
    expected = good["artifacts"]["autotest_review"]["reviewed_relations_sha256"]
    assert "/artifacts/autotest_review/reviewed_relations_sha256" in refused["message"] and expected in refused["message"], refused
    assert "relations-digest" in refused["message"] and _journal(run_root) == before
    assert_refused_once(run_root, before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    code, answered = _api(*response, "--artifact", _put(tmp / "review.json", good), "--transport-attempts", "2")
    assert code == 0, answered


def test_a_journal_written_before_the_package_still_replays(tmp_path: Path, monkeypatch) -> None:
    """A run whose generator request was published without the published plan and header (the old flow) is read as before."""
    from tools import pilot_state

    run = _generated_run(tmp_path, generated=False)
    plan = json.loads(Path(run["plan"]).read_bytes())
    fragment = json.loads(Path(run["fragments"][0]).read_bytes())
    with monkeypatch.context() as old:
        old.setattr(pilot_state, "_expected_model_inputs", lambda *_args: None)  # the code before FaT-DG
        pilot_state.publish_model_request(run["run_root"], run["attempt_id"], f"tc-generator:{fragment['batch_id']}", model_id="model-generator",
                                          invocation_id="invoke-old", input_digests=[run["marker_digest"], json.loads(Path(run["context"]).read_bytes())["digest"],
                                                                                     plan["digest"], plan["header_digest"]])
    state = pilot_state.derive_state(run["run_root"])
    request = next(event for event in state["events"] if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id", "").startswith("tc-generator:"))
    assert pilot_state.read_model_request(run["run_root"], run["attempt_id"], request["stage_instance_id"], request["artifact_digest"])["input_digests"][2] == plan["digest"]
    # the cycle of the agent does not drive such a run: its steps come from the published plan, which the old flow never had
    assert prepare_stage(run["run_root"], request["stage_instance_id"])[1]["code"] == "STAGE_UNKNOWN"


def test_a_case_on_a_requirement_of_another_batch_is_refused_before_the_write(tmp_path: Path) -> None:
    """The whole fragment check runs at submit against the published plan/header, not only in assemble."""
    from tools.batch_assembly import with_digest

    run = _generated_run(tmp_path, generated=False)
    _publish_inputs(run)
    fragment, other = (json.loads(Path(path).read_bytes()) for path in run["fragments"])
    stage = f"tc-generator:{fragment['batch_id']}"
    assert prepare_stage(run["run_root"], stage)[0] == 0
    bad = {key: item for key, item in fragment.items() if key != "digest"}
    bad["test_cases"] = [dict(bad["test_cases"][0], requirement_ids=[*bad["test_cases"][0]["requirement_ids"], other["requirements"][0]["requirement_id"]])]
    before = _journal(run["run_root"])
    code, refused = submit_stage(run["run_root"], stage, with_digest(bad))
    assert code == 2 and refused["code"] == "BATCH_PARTITION_CONFLICT" and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    code, answered = submit_stage(run["run_root"], stage, run["fragments"][0], "--transport-attempts", "2")
    assert code == 0, answered


def test_a_plan_with_a_context_that_is_not_of_the_attempt_is_refused(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.batch_assembly import with_digest

    run = _generated_run(tmp_path, generated=False)
    header = {key: item for key, item in json.loads(Path(run["header"]).read_bytes()).items() if key != "digest"}
    pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "header", header)
    plan = {key: item for key, item in json.loads(Path(run["plan"]).read_bytes()).items() if key != "digest"}
    plan["batches"] = [dict(row, context_receipt_digest="sha256:" + "5" * 64) for row in plan["batches"]]
    before = _journal(run["run_root"])
    context = json.loads(Path(run["context"]).read_bytes())["digest"]
    with pytest.raises(ValueError, match="GENERATOR_INPUT_CONTEXT") as refused:
        pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "plan", with_digest(plan))
    assert context in str(refused.value) and _journal(run["run_root"]) == before
