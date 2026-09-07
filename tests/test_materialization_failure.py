from pathlib import Path

import pytest

from tests.test_generated_delta import _add_second_file, _inputs


def test_partial_materialization_publishes_readback_delta_and_plan_before_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import materialize_delta
    from tools.pilot_state import read_attempt_receipt
    from tools.schema_validation import schema_diagnostics

    inputs = _inputs(tmp_path, mutate=_add_second_file)
    baseline, automation, review, document, run_root, attempt_id = inputs
    original_remove = generated_delta.remove_confined_bytes_if_equal
    ordering_checked = False

    def remove_after_durable_evidence(*args, **kwargs):
        nonlocal ordering_checked
        durable = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
        plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
        assert durable["facts"] == {"completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False}
        assert all("disposition" not in row for row in durable["files"])
        assert plan["stage"] == "disposition-plan"
        ordering_checked = True
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(generated_delta, "remove_confined_bytes_if_equal", remove_after_durable_evidence)
    delta = materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, fail_after=1)
    assert delta["facts"] == {"completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False}
    assert ordering_checked
    assert [item["materialization"] for item in delta["files"]] == ["MATERIALIZED", "NOT_MATERIALIZED"]
    assert all("disposition" not in item for item in delta["files"])
    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"] == delta
    dispositions = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [item["disposition"] for item in dispositions["files"]] == ["CLEANED", "NOT_MATERIALIZED"]
    assert materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, fail_after=1) == delta
    assert schema_diagnostics(delta, Path("schemas/generated-delta.schema.json"), Path.cwd()) == []
    assert schema_diagnostics(delta["files"][0], Path("schemas/materialization-receipt.schema.json"), Path.cwd()) == []
    assert schema_diagnostics(dispositions, Path("schemas/disposition-receipt.schema.json"), Path.cwd()) == []


def test_execution_readiness_requires_exact_complete_readback_bytes(tmp_path: Path):
    from tools.generated_delta import execution_readiness, materialize_delta

    inputs = _inputs(tmp_path)
    baseline, automation, review, document, run_root, attempt_id = inputs
    complete = materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    assert execution_readiness(tmp_path, complete, run_root, attempt_id) == {"ready": True}
    (tmp_path / "tests" / "test_products.py").write_bytes(b"user drift")
    assert execution_readiness(tmp_path, complete, run_root, attempt_id)["ready"] is False


def test_unbound_generated_delta_cannot_authorize_disposition_plan(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, _delta, _file_row, _reviewed_generated_files, materialize_delta
    from tools.pilot_state import _sealed, publish_attempt_receipt, publish_phase5_receipt, read_effective_canonical

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, interrupt_after=1)
    generated, automation_digest, review_digest = _reviewed_generated_files(automation, review)
    source = generated[0]
    content = source["content"].encode("utf-8")
    row = _file_row(source["file_id"], source["path"], content, baseline, automation_digest, review_digest, "MATERIALIZED")
    effective = read_effective_canonical(run_root, attempt_id)
    delta = _delta(project=tmp_path, module=tmp_path, test_root=baseline["test_root"], baseline=baseline, automation_digest=automation_digest, review_digest=review_digest, effective_canonical_digest=effective["document_digest"], effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"], files=[row], facts={"completion": "COMPLETE", "verification": None, "accepted": None})
    publish_attempt_receipt(run_root, attempt_id, "generated-delta", {"delta": delta})
    plan_row = {key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")} | {"operation": "RETAIN_IF_EXACT", "requested_disposition": "RETAINED", "pre_effect_state": "EXACT"}
    plan = _sealed({"schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [plan_row]})
    with pytest.raises(ValueError):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", plan)


def test_materialization_blocks_revision_two_controller_boundary(tmp_path: Path):
    from tools.generated_delta import materialize_delta
    from tools.pilot_state import open_automation_review_boundary

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    with pytest.raises(ValueError, match="automation revision two is forbidden after materialization"):
        open_automation_review_boundary(run_root, attempt_id, {"automation_revision": 2})


def test_ownership_interruption_already_blocks_revision_two(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, materialize_delta
    from tools.pilot_state import open_automation_review_boundary

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, interrupt_after=1)
    with pytest.raises(ValueError, match="automation revision two is forbidden after materialization"):
        open_automation_review_boundary(run_root, attempt_id, {"automation_revision": 2})


@pytest.mark.parametrize("forgery", ["facts", "content_digest", "ownership_digest", "order"])
def test_event_bound_ownership_rejects_forged_generated_delta_before_publication(tmp_path: Path, forgery: str):
    from tools.generated_delta import GeneratedDeltaError, _delta, _file_row, _reviewed_generated_files, materialize_delta
    from tools.pilot_state import publish_generated_delta, read_effective_canonical

    inputs = _inputs(tmp_path, mutate=_add_second_file)
    baseline, automation, review, document, run_root, attempt_id = inputs
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, interrupt_after=1)
    generated, automation_digest, review_digest = _reviewed_generated_files(automation, review)
    rows = [
        _file_row(item["file_id"], item["path"], item["content"].encode("utf-8"), baseline, automation_digest, review_digest, "MATERIALIZED")
        for item in generated
    ]
    facts = {"completion": "COMPLETE", "verification": None, "accepted": None}
    if forgery == "facts":
        facts = {"completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False}
    elif forgery == "content_digest":
        rows[0]["content_digest"] = "sha256:" + "f" * 64
    elif forgery == "ownership_digest":
        rows[0]["ownership_digest"] = "sha256:" + "e" * 64
    elif forgery == "order":
        rows.reverse()
    effective = read_effective_canonical(run_root, attempt_id)
    forged = _delta(project=tmp_path, module=tmp_path, test_root=baseline["test_root"], baseline=baseline, automation_digest=automation_digest, review_digest=review_digest, effective_canonical_digest=effective["document_digest"], effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"], files=rows, facts=facts)
    with pytest.raises(ValueError, match="invalid generated delta receipt"):
        publish_generated_delta(run_root, attempt_id, forged)


def test_cases_only_attempt_never_creates_ownership_or_test_bytes(tmp_path: Path):
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automated_document, automation as automation_artifact, host_evidence, review as review_artifact
    from tools.generated_delta import GeneratedDeltaError, materialize_delta
    from tools.run_pipeline import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    document = automated_document()
    automation = automation_artifact(document)
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, "cases-only-v1", {"request_id": "cases-only", "execution_requested": False}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    review = review_artifact(document, automation, host_evidence("cases-only-review"), "ПРИНЯТО", session_id="cases-only-review")
    with pytest.raises(GeneratedDeltaError, match="local-pilot execution authorization is absent"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert not (run_root / "materialization-ownership" / f"{attempt_id}.json").exists()


@pytest.mark.parametrize("published", [False, True])
def test_materialization_recovers_exact_raw_delta_events_without_rewriting(tmp_path: Path, published: bool):
    from tools.generated_delta import GeneratedDeltaError, _delta, _file_row, _reviewed_generated_files, materialize_delta
    from tools.pilot_state import append_event, derive_state, publish_attempt_receipt, read_effective_canonical

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, interrupt_after=1)
    generated, automation_digest, review_digest = _reviewed_generated_files(automation, review)
    row = _file_row(generated[0]["file_id"], generated[0]["path"], generated[0]["content"].encode("utf-8"), baseline, automation_digest, review_digest, "MATERIALIZED")
    effective = read_effective_canonical(run_root, attempt_id)
    delta = _delta(project=tmp_path, module=tmp_path, test_root=baseline["test_root"], baseline=baseline, automation_digest=automation_digest, review_digest=review_digest, effective_canonical_digest=effective["document_digest"], effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"], files=[row], facts={"completion": "COMPLETE", "verification": None, "accepted": None})
    raw = publish_attempt_receipt(run_root, attempt_id, "generated-delta", {"delta": delta})
    if published:
        append_event(run_root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=raw["digest"])
    path = tmp_path / "tests" / "test_products.py"
    before = path.read_bytes()
    assert materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id) == delta
    assert path.read_bytes() == before
    events = [item for item in derive_state(run_root)["events"] if item.get("artifact_digest") == raw["digest"]]
    assert [item["event_type"] for item in events] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]


@pytest.mark.parametrize("published", [False, True])
def test_disposition_recovers_exact_raw_final_events_without_effects(tmp_path: Path, published: bool, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import apply_dispositions, materialize_delta
    from tools.pilot_state import _sealed, append_event, derive_state, publish_attempt_receipt, publish_phase5_receipt

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    delta = materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    row = delta["files"][0]
    plan_row = {key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")} | {"operation": "RETAIN_IF_EXACT", "requested_disposition": "RETAINED", "pre_effect_state": "EXACT"}
    plan = _sealed({"schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [plan_row]})
    publish_phase5_receipt(run_root, attempt_id, "disposition-plan", plan)
    final_row = {key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")} | {"disposition": "RETAINED"}
    final = _sealed({"schema_version": "1.0.0", "stage": "dispositions", "disposition_plan_digest": plan["digest"], "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [final_row]})
    raw = publish_attempt_receipt(run_root, attempt_id, "disposition-receipt", {"payload": final})
    if published:
        append_event(run_root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=raw["digest"])
    remove_calls = 0

    def no_remove(*_args, **_kwargs):
        nonlocal remove_calls
        remove_calls += 1
        raise AssertionError("exact final replay must not remove")

    monkeypatch.setattr(generated_delta, "remove_confined_bytes_if_equal", no_remove)
    replay = apply_dispositions(tmp_path, delta, {row["path"]: "RETAINED"}, run_root=run_root, attempt_id=attempt_id, verification="PASS")
    assert replay["files"][0]["disposition"] == "RETAINED"
    assert remove_calls == 0
    events = [item for item in derive_state(run_root)["events"] if item.get("artifact_digest") == raw["digest"]]
    assert [item["event_type"] for item in events] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
