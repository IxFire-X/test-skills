"""FaT-DG: the digest traps of the 28.10 campaign, each caught before anything is written, with the right value in the
refusal; the stage keeps its one retry. The chain runs through `python -m tools.api` commands."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.test_review_requested import _api, _generated_run, _journal, _put


def _sha_file(path: str) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _publish_inputs(run: dict) -> tuple[str, str]:
    run_args = ("--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    header = json.loads(Path(run["header"]).read_bytes())
    header.pop("digest", None)
    code, published_header = _api("publish-generator-input", *run_args, "--kind", "header", "--value", _put(run["tmp"] / "header-raw.json", header))
    assert code == 0, published_header
    code, published_plan = _api("publish-generator-input", *run_args, "--kind", "plan", "--value", run["plan"])
    assert code == 0, published_plan
    return published_plan["result"]["digest"], published_header["result"]["digest"]


def _generator_request(run: dict, batch_id: str, plan_digest: str, header_digest: str, transport: str = "") -> tuple[int, dict]:
    context = json.loads(Path(run["context"]).read_bytes())["digest"]
    return _api("model-request", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{batch_id}",
                "--model-id", "model-generator", "--invocation-id", "invoke-generator",
                "--input-digest", run["marker_digest"], "--input-digest", context, "--input-digest", plan_digest, "--input-digest", header_digest)


def test_one_digest_for_plan_and_header(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    plan = json.loads(Path(run["plan"]).read_bytes())
    assert plan_digest == plan["digest"] and header_digest == plan["header_digest"]
    # the same value again changes nothing; a bytes digest put into the value is refused with the right one
    before = _journal(run["run_root"])
    assert _publish_inputs(run) == (plan_digest, header_digest) and _journal(run["run_root"]) == before
    header = json.loads(Path(run["header"]).read_bytes())
    header["digest"] = _sha_file(run["header"])
    code, refused = _api("publish-generator-input", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "header",
                         "--value", _put(run["tmp"] / "header-bytes.json", header))
    assert code == 2 and refused["message"].startswith("GENERATOR_INPUT_DIGEST") and header_digest in refused["message"], refused


def test_commons_lang_b1a27c0b_bytes_digest_of_the_header(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    batch = json.loads(Path(run["fragments"][0]).read_bytes())["batch_id"]
    before = _journal(run["run_root"])
    code, refused = _generator_request(run, batch, plan_digest, _sha_file(run["header"]))
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH", refused
    assert "вход 4" in refused["message"] and header_digest in refused["message"] and "publish-generator-input --kind header" in refused["message"], refused
    assert "вход 3" not in refused["message"] and _journal(run["run_root"]) == before
    code, requested = _generator_request(run, batch, plan_digest, header_digest)
    assert code == 0, requested


def test_jpetstore_05b4a6e3_bytes_digests_of_plan_and_header(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    batch = json.loads(Path(run["fragments"][0]).read_bytes())["batch_id"]
    before = _journal(run["run_root"])
    code, refused = _generator_request(run, batch, _sha_file(run["plan"]), _sha_file(run["header"]))
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH", refused
    assert plan_digest in refused["message"] and header_digest in refused["message"] and "вход 3" in refused["message"], refused
    assert _journal(run["run_root"]) == before


def test_a_request_before_the_plan_is_published_names_the_command(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    plan = json.loads(Path(run["plan"]).read_bytes())
    batch = json.loads(Path(run["fragments"][0]).read_bytes())["batch_id"]
    code, refused = _generator_request(run, batch, plan["digest"], plan["header_digest"])
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and "publish-generator-input --kind plan" in refused["message"], refused


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
    plan_digest, header_digest = _publish_inputs(run)
    fragment = json.loads(Path(run["fragments"][0]).read_bytes())
    assert _generator_request(run, fragment["batch_id"], plan_digest, header_digest)[0] == 0
    bad = _uncovered(fragment)
    before = _journal(run["run_root"])
    response = ("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{fragment['batch_id']}")
    code, refused = _api(*response, "--artifact", _put(run["tmp"] / "bad.json", bad))
    assert code == 2 and refused["code"] == "BATCH_UNCOVERED_REQUIREMENT", refused
    assert bad["requirements"][1]["requirement_id"] in refused["message"] and "--transport-attempts 2" in refused["message"], refused
    assert _journal(run["run_root"]) == before
    # the one retry of the stage is still there
    code, answered = _api(*response, "--artifact", run["fragments"][0], "--transport-attempts", "2")
    assert code == 0, answered


def test_assemble_and_validate_fragment_agree_on_coverage(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    bad = _put(run["tmp"] / "bad.json", _uncovered(fragments[0]))
    code, validated = _api("validate-fragment", "--fragment", bad, "--plan", run["plan"], "--header", run["header"], "--context-receipt", run["context"])
    code_assembled, assembled = _api("assemble", "--header", run["header"], "--plan", run["plan"], "--fragment", bad, "--fragment", run["fragments"][1],
                                     "--out-candidate", str(run["tmp"] / "c.json"), "--out-receipt", str(run["tmp"] / "r.json"))
    missing = _uncovered(fragments[0])["requirements"][1]["requirement_id"]
    assert code == code_assembled == 2 and validated["code"] == assembled["code"] == "BATCH_UNCOVERED_REQUIREMENT", (validated, assembled)
    assert missing in validated["message"] and missing in assembled["message"]
    from tools.canonical_document import uncovered_requirements

    # the same rule assemble applies to the whole document (SEMANTIC_UNCOVERED_REQUIREMENT)
    assert [row["requirement_id"] for row in _uncovered(fragments[0])["requirements"]][1] == _uncovered(fragments[0])["requirements"][
        uncovered_requirements(_uncovered(fragments[0])["requirements"], fragments[0]["test_cases"])[0]]["requirement_id"]
    code, validated = _api("validate-fragment", "--fragment", run["fragments"][0], "--plan", run["plan"], "--header", run["header"], "--context-receipt", run["context"])
    code_assembled, assembled = _api("assemble", "--header", run["header"], "--plan", run["plan"], *[flag for path in run["fragments"] for flag in ("--fragment", path)],
                                     "--out-candidate", str(run["tmp"] / "c.json"), "--out-receipt", str(run["tmp"] / "r.json"))
    assert code == code_assembled == 0, (validated, assembled)


def test_petclinic_rest_523ec561_reviewed_relations_digest_before_the_write(tmp_path: Path) -> None:
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automated_document, automation, review
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical
    from tools.run_pipeline import run_phase_one_spine

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
    code, answered = _api(*response, "--artifact", _put(tmp / "review.json", good), "--transport-attempts", "2")
    assert code == 0, answered


def test_a_journal_written_before_the_package_still_replays(tmp_path: Path, monkeypatch) -> None:
    """A run whose generator request was published without publish-generator-input (the old flow) reads as before."""
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
    code, answered = _api("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", request["stage_instance_id"],
                          "--artifact", run["fragments"][0])
    assert code == 0, answered
    code, inputs = _api("step-inputs", "--run-root", str(run["run_root"]), "--stage", request["stage_instance_id"])
    assert code == 0, inputs


def test_a_case_on_a_requirement_of_another_batch_is_refused_before_the_write(tmp_path: Path) -> None:
    """The whole fragment check runs at model-response against the published plan/header, not only in assemble."""
    from tools.batch_assembly import with_digest

    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    fragment, other = (json.loads(Path(path).read_bytes()) for path in run["fragments"])
    assert _generator_request(run, fragment["batch_id"], plan_digest, header_digest)[0] == 0
    bad = {key: item for key, item in fragment.items() if key != "digest"}
    bad["test_cases"] = [dict(bad["test_cases"][0], requirement_ids=[*bad["test_cases"][0]["requirement_ids"], other["requirements"][0]["requirement_id"]])]
    before = _journal(run["run_root"])
    response = ("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{fragment['batch_id']}")
    code, refused = _api(*response, "--artifact", _put(run["tmp"] / "foreign.json", with_digest(bad)))
    assert code == 2 and refused["code"] == "BATCH_PARTITION_CONFLICT" and "--transport-attempts 2" in refused["message"], refused
    assert _journal(run["run_root"]) == before
    code, answered = _api(*response, "--artifact", run["fragments"][0], "--transport-attempts", "2")
    assert code == 0, answered


def _publish(run: dict, kind: str, value: dict) -> tuple[int, dict]:
    return _api("publish-generator-input", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", kind,
                "--value", _put(run["tmp"] / f"{kind}-try.json", value))


def test_a_plan_before_its_header_is_refused(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    before = _journal(run["run_root"])
    code, refused = _publish(run, "plan", json.loads(Path(run["plan"]).read_bytes()))
    assert code == 2 and refused["message"].startswith("GENERATOR_INPUT_ORDER") and "--kind header" in refused["message"], refused
    assert _journal(run["run_root"]) == before


def test_a_plan_with_another_header_digest_is_refused_with_the_published_one(tmp_path: Path) -> None:
    from tools.batch_assembly import with_digest

    run = _generated_run(tmp_path, generated=False)
    header = json.loads(Path(run["header"]).read_bytes())
    code, published = _publish(run, "header", header)
    assert code == 0, published
    plan = {key: item for key, item in json.loads(Path(run["plan"]).read_bytes()).items() if key != "digest"}
    plan["batches"] = [dict(row, header_digest="sha256:" + "4" * 64) for row in plan["batches"]]
    before = _journal(run["run_root"])
    code, refused = _publish(run, "plan", with_digest(plan))
    assert code == 2 and refused["message"].startswith("GENERATOR_INPUT_HEADER") and published["result"]["digest"] in refused["message"], refused
    assert _journal(run["run_root"]) == before
    code, ok = _publish(run, "plan", json.loads(Path(run["plan"]).read_bytes()))  # the right plan still goes: no conflict was written
    assert code == 0, ok


def test_a_plan_with_a_context_that_is_not_of_the_attempt_is_refused(tmp_path: Path) -> None:
    from tools.batch_assembly import with_digest

    run = _generated_run(tmp_path, generated=False)
    assert _publish(run, "header", json.loads(Path(run["header"]).read_bytes()))[0] == 0
    plan = {key: item for key, item in json.loads(Path(run["plan"]).read_bytes()).items() if key != "digest"}
    plan["batches"] = [dict(row, context_receipt_digest="sha256:" + "5" * 64) for row in plan["batches"]]
    before = _journal(run["run_root"])
    code, refused = _publish(run, "plan", with_digest(plan))
    context = json.loads(Path(run["context"]).read_bytes())["digest"]
    assert code == 2 and refused["message"].startswith("GENERATOR_INPUT_CONTEXT") and context in refused["message"], refused
    assert _journal(run["run_root"]) == before
