"""FaT-AU5: the inputs of a model request are checked once (audit U1) and a marker in parts is reconciled with OpenSpec on
the requirements answer, before it is written (audit D4)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_once

from tests.test_digest_traps import _api, _journal, _put
from tests.test_marker_parts import _marker, _publish_part, _run
from tools import pilot_state

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in

SPEC = "## Requirements\n### Requirement: Search\nSearch the catalog.\n#### Scenario: Found\n- WHEN search\n- THEN results\n"
SPEC_PATH = "openspec/specs/catalog/spec.md"


def _spec_run(tmp_path: Path) -> tuple[Path, str, dict, list[dict]]:
    (tmp_path / SPEC_PATH).parent.mkdir(parents=True)
    (tmp_path / SPEC_PATH).write_bytes(SPEC.encode("utf-8"))
    run = _run(tmp_path)
    assert any(item["project_path"].replace("\\", "/") == SPEC_PATH for receipt in run[3] for item in receipt["files"])
    return run


def _request_requirements(root: Path, attempt_id: str, baseline: dict) -> None:
    pilot_state.publish_model_request(root, attempt_id, "context-marker:requirements", model_id="model-context", invocation_id="context-req",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])


def _responses(root: Path) -> list[str]:
    return [str(event.get("stage_instance_id")) for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]


def test_d4_requirements_that_miss_openspec_are_refused_before_the_write(tmp_path: Path) -> None:
    """Before FaT-AU5 the answer was written and only the merge failed — for good, the parts being recorded already."""
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    root, attempt_id, baseline, _receipts = _spec_run(tmp_path)
    _request_requirements(root, attempt_id, baseline)
    with pytest.raises(ValueError, match="OpenSpec reconciliation failed: context-marker:requirements: .*OPENSPEC_MISSING.*--transport-attempts 2"):
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _marker([REQUIREMENTS_PLACEHOLDER]))
    assert _responses(root) == []


def test_d4_requirements_that_follow_openspec_merge(tmp_path: Path) -> None:
    from tools.build_context import build_context
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
    from tools.context_marker_parts import merge_parts

    root, attempt_id, baseline, receipts = _spec_run(tmp_path)
    entry = {"path": SPEC_PATH, "content": SPEC, "sha256": "sha256:" + hashlib.sha256(SPEC.encode("utf-8")).hexdigest()}
    rows = build_context(PACK_ROOT, docs_snapshot=[entry])["artifacts"]["analytics_documentation"]["requirements"]
    _request_requirements(root, attempt_id, baseline)
    requirements = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements",
                                                            _marker([REQUIREMENTS_PLACEHOLDER], requirements=rows))
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — x"], requirements=rows))
    merged = merge_parts(root)
    assert merged["artifact"]["artifacts"]["analytics_documentation"]["requirements"] == rows


def _automation_run(tmp_path: Path) -> tuple[Path, str, tuple[str, ...]]:
    from tests.helpers import build_phase_two_baseline
    from tools.run_pipeline import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    started = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "au5", "execution_requested": True}, identity, baseline, {})
    run_root, attempt_id = Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]
    return run_root, attempt_id, ("--run-root", str(run_root), "--attempt", attempt_id)


def test_u1_every_refusal_of_the_removed_automation_block_still_holds(tmp_path: Path) -> None:
    """The removed block refused an automation request whose predecessor is unreadable or whose inputs differ; both stay."""
    from tests.test_automation_revision_budget import automated_document, automation
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical

    run_root, attempt_id, run = _automation_run(tmp_path)
    tmp = run_root / "tmp"
    generator = ("model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1")
    before = _journal(run_root)
    code, refused = _api(*generator, "--input-digest", "sha256:" + "a" * 64, "--input-digest", "sha256:" + "b" * 64)
    assert code != 0 and refused["status"] != "ok" and _journal(run_root) == before, refused  # no effective canonical yet
    assert_refused_once(run_root, before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    document = automated_document()
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    effective = _api("read-effective", *run)[1]["result"]
    assert _api(*generator, "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])[0] == 0
    artifact = automation(document, 1, predecessor=None, correction_review=None, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    assert _api("model-response", *run, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))[0] == 0
    reviewer = ("model-request", *run, "--stage", "autotest-reviewer:r1", "--model-id", "model-automation-reviewer")
    before = _journal(run_root)
    code, refused = _api(*reviewer, "--invocation-id", "reviewer-1", "--input-digest", "sha256:" + "a" * 64, "--input-digest", "sha256:" + "b" * 64)
    assert code != 0 and "OUT_OF_ORDER" not in json.dumps(refused) and _journal(run_root) == before, refused  # no review boundary yet
    assert_refused_once(run_root, before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    statement = ("--fresh-context", "yes", "--reviewer-invocation-id", "automation-reviewer-subagent-1")
    assert _api("automation-boundary-facts", *run, "--revision", "1", *statement, "--out", str(tmp / "facts.json"))[0] == 0
    assert _api("open-automation-boundary", *run, "--facts", str(tmp / "facts.json"))[0] == 0
    facts = json.loads((tmp / "facts.json").read_bytes())
    assert _api("review-requested", *run, "--stage", "autotest-reviewer:r1", "--digest", facts["automation_digest"])[0] == 0
    boundary = _api("read-receipt", *run, "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")[1]["result"]["record"]
    reviewer = (*reviewer, "--invocation-id", boundary["reviewer_invocation_id"])
    code, refused = _api(*reviewer, "--input-digest", boundary["digest"], "--input-digest", boundary["automation_digest"])
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and "automation-review-boundary-r1" in refused["message"], refused
    assert _api(*reviewer, "--input-digest", boundary["automation_digest"], "--input-digest", boundary["digest"])[0] == 0


def test_a_marker_request_over_an_openspec_receipt_is_published(tmp_path: Path) -> None:
    """Before FaT-AU5 the OpenSpec bytes were read with the project itself as the confined root, which always refuses."""
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    root, attempt_id, baseline, receipts = _spec_run(tmp_path)
    entry = {"path": SPEC_PATH, "content": SPEC, "sha256": "sha256:" + hashlib.sha256(SPEC.encode("utf-8")).hexdigest()}
    from tools.build_context import build_context

    rows = build_context(PACK_ROOT, docs_snapshot=[entry])["artifacts"]["analytics_documentation"]["requirements"]
    _request_requirements(root, attempt_id, baseline)
    requirements = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements",
                                                            _marker([REQUIREMENTS_PLACEHOLDER], requirements=rows))
    index = next(number for number, receipt in enumerate(receipts, start=1)
                 if any(item["project_path"].replace("\\", "/") == SPEC_PATH for item in receipt["files"]))
    pilot_state.publish_model_request(root, attempt_id, f"context-marker:batch-{index:03d}", model_id="model-context", invocation_id="part",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"],
                                                     receipts[index - 1]["digest"], requirements["content_digest"]])


def test_an_openspec_path_that_leaves_openspec_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, attempt_id, baseline, receipts = _spec_run(tmp_path)
    (tmp_path / "src" / "spec.md").write_bytes(SPEC.encode("utf-8"))
    forged = {**receipts[0], "files": [{"project_path": "openspec/specs/../../src/spec.md",
                                        "content_digest": "sha256:" + hashlib.sha256(SPEC.encode("utf-8")).hexdigest()}]}
    monkeypatch.setattr(pilot_state, "read_context_selection", lambda *_args: forged)
    state = pilot_state.derive_state(root)
    attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
    with pytest.raises(ValueError, match="confined root"):
        pilot_state._context_marker_snapshots(tmp_path, root, state, attempt, "context-marker:requirements",
                                              [baseline["requirements"]["digest"], baseline["inventory_digest"]], [receipts[0]["digest"]])
