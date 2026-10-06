import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _effective_bundle(document: dict) -> dict:
    from tools.canonical_document import document_sha256

    return {
        "document_id": document["document_id"], "revision": document["revision"], "csv_profile": "zephyr-scale-step-row-24-v4",
        "json_path": "test-output/effective.json", "preview_path": "test-output/effective.html", "csv_path": "test-output/effective.zephyr-scale.csv",
        "document_sha256": document_sha256(document), "preview_sha256": "sha256:" + "1" * 64, "csv_sha256": "sha256:" + "2" * 64,
    }


def _effective_bundle_digest(document: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(_effective_bundle(document), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n").hexdigest()


def _reviewer_protocol_inputs(
    document: dict,
    run_root: Path,
    attempt_id: str,
    *,
    bind_boundary: bool = True,
    bundle_root: Path | None = None,
    complete: bool = True,
    byte_budget: int = 1000000,
):
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import (
        open_reviewer_session,
        publish_unreviewed_candidate,
    )
    from tools.pilot_state import (
        append_event,
        derive_state,
        publish_context_selection,
        publish_model_request,
        publish_model_stage_artifact,
    )
    from tools.project_inventory import (
        read_execution_baseline,
        read_inventory_receipt,
        select_context_batches,
    )

    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(
        run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json")
    )
    inventory = read_inventory_receipt(
        run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json")
    )
    context = publish_context_selection(
        run_root,
        attempt_id,
        select_context_batches(inventory, Path(attempt["project"]), [], byte_budget=64 * 1024)[0]["receipt"],
    )

    marker = {
        "schema_version": "5.0.0", "stage": "context-marker",
        "artifacts": {
            "analytics_documentation": {"requirements": document["source_requirements"]},
            "source_code_and_diff": {"sources": ["fixture — durable model input"]},
        },
        "warnings": [],
    }
    invocation_suffix = attempt_id[:12]
    generator_invocation_id = f"phase5-generator-{invocation_suffix}"
    reviewer_invocation_id = f"phase5-reviewer-{invocation_suffix}"
    publish_model_request(
        run_root, attempt_id, "context-marker:baseline",
        model_id="model-context", invocation_id=f"context-{attempt_id}",
        input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], context["digest"]],
    )
    marker_publication = publish_model_stage_artifact(
        run_root, attempt_id, "context-marker:baseline", marker,
    )
    fragments = []
    for index, source in enumerate(document["source_requirements"], start=1):
        batch_id = f"BATCH-fixture-{'a' if index == 1 else 'b'}"
        fragment = {
            "schema_version": "1.0.0", "status": "COMPLETE",
            "batch_id": batch_id, "namespace": f"B{index}",
            "plan_digest": "sha256:" + "1" * 64,
            "header_digest": "sha256:" + "2" * 64,
            "context_receipt_digest": context["digest"], "context_receipt": context,
            "owned_source_requirement_ids": [source["source_requirement_id"]],
            "requirements": [], "source_to_canonical_mappings": [],
            "operation_capabilities": [], "test_cases": [], "diagnostics": [],
        }
        fragment["digest"] = "sha256:" + hashlib.sha256(json.dumps(fragment, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        publish_model_request(
            run_root, attempt_id, f"tc-generator:{batch_id}",
            model_id="model-generator",
            invocation_id=(generator_invocation_id if index == 1 else f"{generator_invocation_id}-{index}"),
            input_digests=[
                marker_publication["content_digest"], context["digest"],
                fragment["plan_digest"], fragment["header_digest"],
            ],
        )
        fragments.append(publish_model_stage_artifact(
            run_root, attempt_id, f"tc-generator:{batch_id}", fragment,
        ))

    package = {
        "candidate_digest": document_sha256(document), "candidate_receipt_digest": "sha256:" + "b" * 64,
        "assembly_digest": "sha256:" + "c" * 64,
        "context_marker_output_digest": marker_publication["content_digest"],
        "generator_batches": [
            {"batch_id": item["artifact"]["batch_id"], "digest": item["content_digest"], "context_receipt_digest": context["digest"]}
            for item in fragments
        ],
        "inventory_digest": inventory["digest"],
        "context_receipt_digests": [context["digest"]],
        "base_package_byte_count": context["byte_count"] + marker_publication["byte_count"] + sum(item["byte_count"] for item in fragments),
    }
    package["package_digest"] = "sha256:" + hashlib.sha256(json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidate_receipt = publish_unreviewed_candidate(
        document, bundle_root or run_root / "candidate-bundle",
        run_root=run_root, attempt_id=attempt_id,
    )
    from tests.helpers import review_payload, complete_review_parts
    from tools.pilot_state import prepare_review
    if not bind_boundary:
        raise ValueError("review requires its durable boundary")
    prepare_review(run_root, attempt_id, review_payload(Path(attempt["project"]), document, package=package),
                   input_byte_budget=byte_budget, response_reserve_bytes=1000,
                   instructions="Review original requirements, cases and cross-case consistency.", session_id="phase5-effective")
    if not complete:
        from tools.pilot_state import read_reviewer_session_ledger
        return dict(read_reviewer_session_ledger(run_root, attempt_id)), package, None, candidate_receipt
    completed = complete_review_parts(run_root, attempt_id)
    return completed["session"], package, completed["output"], candidate_receipt


def _published_reviewer_ledger(run_root: Path, attempt_id: str, document: dict, *, bind_boundary: bool = True) -> str:
    from tools.pilot_state import publish_reviewer_session_ledger

    session, _package, _review, _receipt = _reviewer_protocol_inputs(
        document, run_root, attempt_id, bind_boundary=bind_boundary,
    )
    return publish_reviewer_session_ledger(run_root, attempt_id, session)["digest"]


def _inputs(
    tmp_path: Path,
    mutate=None,
    *,
    effective: bool = True,
    automation_lifecycle: bool = True,
):
    from tests.helpers import build_phase_two_baseline
    from tools.run_pipeline import run_phase_one_spine
    from tests.test_automation_revision_budget import automated_document, automation as automation_artifact, durable_boundary, host_evidence, review as review_artifact

    (tmp_path / "tests").mkdir(exist_ok=True)
    document = automated_document()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "delta-local", "execution_requested": True}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    from tools.pilot_state import publish_effective_canonical

    bundle = _effective_bundle(document)
    bundle_digest = _effective_bundle_digest(document)
    if effective:
        selection = publish_effective_canonical(run_root, attempt_id, document, bundle, _published_reviewer_ledger(run_root, attempt_id, document))
        bundle_digest = selection["record"]["effective_bundle_receipt_digest"]
    automation = automation_artifact(document, effective_bundle_receipt_digest=bundle_digest)
    if mutate is not None:
        mutate(automation)
    evidence = host_evidence("delta-review")
    boundary = evidence
    if effective and automation_lifecycle:
        _run_root, _attempt_id, boundary = durable_boundary(tmp_path, automation, "delta-review", evidence, run_root=run_root, attempt_id=attempt_id)
    review = review_artifact(document, automation, boundary, "ПРИНЯТО", session_id="delta-review",
                            run_root=run_root if effective and automation_lifecycle else None,
                            attempt_id=attempt_id if effective and automation_lifecycle else None)
    return baseline, automation, review, document, run_root, attempt_id


def _publish_automation_review_lifecycle(
    run_root: Path,
    attempt_id: str,
    automation: dict,
    boundary: dict,
    review: dict,
) -> None:
    from tools.pilot_state import read_review_aggregate
    aggregate = read_review_aggregate(run_root, attempt_id, f"r{automation['artifacts']['automation_revision']}")
    assert aggregate["output"] == review and aggregate["aggregate"]["complete"]


def _materialize(tmp_path: Path, inputs, **extra):
    from tools.generated_delta import materialize_delta

    baseline, automation, review, document, run_root, attempt_id = inputs
    return materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id, **extra)


def test_automation_request_rejects_wrong_inputs_before_publication(tmp_path: Path):
    from tools.pilot_state import publish_model_request

    *_, run_root, attempt_id = _inputs(tmp_path, automation_lifecycle=False)
    before = (run_root / "events.jsonl").read_bytes()
    requests = sorted((run_root / "model-requests").rglob("*.json"))
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1:part-000001"):
        with pytest.raises(ValueError, match="inputs|input digests|review-plan"):
            publish_model_request(
                run_root, attempt_id, stage, model_id="model-test",
                invocation_id="wrong-inputs", input_digests=["sha256:" + "f" * 64],
            )
        assert (run_root / "events.jsonl").read_bytes() == before
        assert sorted((run_root / "model-requests").rglob("*.json")) == requests


def _published_execution_delta(tmp_path: Path, *, failed: bool) -> tuple[dict, Path, str]:
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts

    run_root, attempt_id, report, request, durable = _execution_facts(
        tmp_path, project=tmp_path, failed=failed,
    )
    _publish_execution(run_root, attempt_id, report, request=request)
    return durable["delta"], run_root, attempt_id


def test_materialization_requires_attempt_owned_effective_selection_before_writes(tmp_path: Path):
    """Phase 5 cannot materialize a caller-supplied canonical document alone."""
    from tools.generated_delta import GeneratedDeltaError

    inputs = _inputs(tmp_path, effective=False)
    with pytest.raises(GeneratedDeltaError, match="effective canonical selection"):
        _materialize(tmp_path, inputs)
    assert not (tmp_path / "tests" / "test_products.py").exists()


def test_effective_selection_rejects_unpublished_or_nonterminal_reviewer_digest(tmp_path: Path):
    from tools.pilot_state import publish_effective_canonical

    _baseline, _automation, _review, document, run_root, attempt_id = _inputs(tmp_path, effective=False)
    with pytest.raises(ValueError, match="invalid effective canonical selection"):
        publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), "sha256:" + "9" * 64)


def test_reviewer_request_rejects_missing_attempt_boundary(tmp_path: Path):
    _baseline, _automation, _review, document, run_root, attempt_id = _inputs(tmp_path, effective=False)
    with pytest.raises(ValueError, match="receipt|boundary"):
        _published_reviewer_ledger(run_root, attempt_id, document, bind_boundary=False)
    assert not any(json.loads(path.read_text(encoding="utf-8"))["stage"] == "tc-reviewer" for path in (run_root / "model-requests").rglob("*.json"))


def test_effective_selection_rejects_noncanonical_reviewer_ledger_bytes(tmp_path: Path):
    from tools.pilot_state import publish_effective_canonical

    _baseline, _automation, _review, document, run_root, attempt_id = _inputs(tmp_path, effective=False)
    ledger_digest = _published_reviewer_ledger(run_root, attempt_id, document)
    ledger_path = run_root / "reviewer-session-ledgers" / attempt_id / "canonical" / f"{ledger_digest.removeprefix('sha256:')}.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid effective canonical selection"):
        publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), ledger_digest)


def test_effective_selection_read_requires_publish_then_readback_events(tmp_path: Path):
    from tools.canonical_document import document_sha256
    from tools.pilot_state import append_event, publish_attempt_receipt, read_effective_canonical

    _baseline, _automation, _review, document, run_root, attempt_id = _inputs(tmp_path, effective=False)
    ledger_digest = _published_reviewer_ledger(run_root, attempt_id, document)
    bundle = _effective_bundle(document)
    receipt = publish_attempt_receipt(run_root, attempt_id, "effective-canonical", {
        "document": document, "document_digest": document_sha256(document),
        "effective_bundle_receipt": bundle, "effective_bundle_receipt_digest": _effective_bundle_digest(document),
        "reviewer_session_digest": ledger_digest,
    })
    append_event(run_root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=receipt["digest"])

    with pytest.raises(ValueError, match="effective canonical event binding"):
        read_effective_canonical(run_root, attempt_id)


def test_effective_selection_is_fixed_and_rejects_conflicting_bundle(tmp_path: Path):
    from tools.pilot_state import publish_effective_canonical, read_effective_canonical

    _baseline, _automation, _review, document, run_root, attempt_id = _inputs(tmp_path)
    bundle = _effective_bundle(document)
    bundle["csv_sha256"] = "sha256:" + "7" * 64
    with pytest.raises(ValueError, match="effective canonical selection"):
        publish_effective_canonical(run_root, attempt_id, document, bundle, read_effective_canonical(run_root, attempt_id)["reviewer_session_digest"])


def test_materialization_rejects_same_document_with_foreign_effective_bundle_digest(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError

    inputs = _inputs(tmp_path)
    _baseline, automation, review, _document, _run_root, _attempt_id = inputs
    foreign = "sha256:" + "f" * 64
    automation["artifacts"]["source"]["effective_bundle_receipt_digest"] = foreign
    review["artifacts"]["autotest_review"]["source"]["effective_bundle_receipt_digest"] = foreign

    with pytest.raises(GeneratedDeltaError, match="effective canonical selection"):
        _materialize(tmp_path, inputs)
    assert not (tmp_path / "tests" / "test_products.py").exists()


@pytest.mark.parametrize("field", ["effective_canonical_digest", "effective_bundle_receipt_digest"])
def test_generated_delta_rejects_tampered_effective_selection_digest(tmp_path: Path, field: str):
    from tools.generated_delta import _seal
    from tools.pilot_state import publish_generated_delta

    inputs = _inputs(tmp_path)
    delta = _materialize(tmp_path, inputs)
    forged = dict(delta)
    forged[field] = "sha256:" + "e" * 64
    forged = _seal(forged)
    with pytest.raises(ValueError, match="invalid generated delta receipt"):
        publish_generated_delta(inputs[4], inputs[5], forged)


def test_actual_phase4_selection_is_consumed_by_phase5_materialization(tmp_path: Path):
    from tests.helpers import build_phase_two_baseline
    from tests.test_automation_revision_budget import automation as automation_artifact, automated_document, durable_boundary, host_evidence, review as review_artifact
    from tools.generated_delta import materialize_delta
    from tools.orchestrate_test_case_revision import orchestrate_revision
    from tools.pilot_state import read_effective_canonical
    from tools.run_pipeline import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    document = automated_document()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "phase4-to-phase5", "execution_requested": True}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    bundle_root = run_root / "phase4-bundle"
    session, package, tc_review, candidate_receipt = _reviewer_protocol_inputs(
        document, run_root, attempt_id, bundle_root=bundle_root,
    )

    selected = orchestrate_revision(
        document, candidate_receipt, package, session, tc_review, bundle_root,
        run_root=run_root, attempt_id=attempt_id,
    )
    assert selected.status == "EFFECTIVE_SELECTED"
    effective = read_effective_canonical(run_root, attempt_id)
    automation = automation_artifact(document, effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"])
    evidence = host_evidence("phase5-review")
    _root, _attempt, boundary = durable_boundary(tmp_path, automation, "phase5-review", evidence, run_root=run_root, attempt_id=attempt_id)
    # Bounded review: the verdict is the controller aggregate of the completed parts.
    static_review = review_artifact(document, automation, boundary, "ПРИНЯТО", session_id="phase5-review", run_root=run_root, attempt_id=attempt_id)
    _publish_automation_review_lifecycle(
        run_root, attempt_id, automation, boundary, static_review,
    )

    delta = materialize_delta(
        tmp_path, tmp_path, baseline, automation, static_review,
        canonical_document=document, run_root=run_root, attempt_id=attempt_id,
    )
    assert delta["effective_canonical_digest"] == effective["document_digest"]
    assert delta["effective_bundle_receipt_digest"] == effective["effective_bundle_receipt_digest"]
    assert (tmp_path / "tests" / "test_products.py").read_text(encoding="utf-8") == "def test_products():\n    assert True\n"


def _add_second_file(automation: dict) -> None:
    content = "def test_second(): pass\n"
    artifacts = automation["artifacts"]
    artifacts["generated_files"].append({
        "file_id": "FILE-second", "path": "tests/test_second.py", "language": "python", "framework": "pytest",
        "content": content, "content_digest": _digest(content.encode("utf-8")),
    })
    artifacts["generated_symbols"].append({
        "file_id": "FILE-second", "symbol_id": "SYMBOL-second",
        "locator": {"kind": "python_module_function", "function_name": "test_second"},
    })
    operation, assertion = artifacts["implementation_relations"]
    artifacts["implementation_relations"] = [
        operation, {**operation, "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
        assertion, {**assertion, "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
    ]


def _add_component_prefix_collision(automation: dict, *, reverse: bool) -> None:
    _add_second_file(automation)
    files = automation["artifacts"]["generated_files"]
    files[1]["path"] = "tests/test_products.py/test_second.py"
    if reverse:
        files.reverse()


def test_materialize_complete_owned_delta_reads_back_every_file_and_retains_after_pass(tmp_path: Path):
    from tools.generated_delta import apply_dispositions, inspect_delta

    inputs = _inputs(tmp_path)
    baseline, automation, review, document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    assert delta["facts"] == {"completion": "COMPLETE", "verification": None, "accepted": None}
    assert delta["files"][0]["materialization"] == "MATERIALIZED"
    assert {
        key: delta["files"][0][key]
        for key in ("baseline_digest", "automation_digest", "review_digest")
    } == {key: delta[key] for key in ("baseline_digest", "automation_digest", "review_digest")}
    assert inspect_delta(tmp_path, delta)["valid"] is True
    retained = apply_dispositions(tmp_path, delta, {delta["files"][0]["path"]: "RETAINED"}, run_root=run_root, attempt_id=attempt_id, verification="PASS")
    assert retained["files"][0]["disposition"] == "RETAINED"


def test_execution_inputs_are_fixed_and_read_back_before_materialization(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError
    from tools.pilot_state import derive_state, read_attempt_receipt, read_execution_inputs

    inputs = _inputs(tmp_path)
    _baseline, automation, review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    durable = read_execution_inputs(run_root, attempt_id)
    assert durable["automation_artifact"] == automation
    assert durable["autotest_review"] == review
    events = derive_state(run_root)["events"]
    input_positions = [
        index for index, event in enumerate(events)
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == durable["digest"]
        and event["event_type"] in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    ownership = read_attempt_receipt(
        run_root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK",
    )["record"]
    ownership_publish = next(
        index for index, event in enumerate(events)
        if event.get("artifact_digest") == ownership["digest"]
        and event["event_type"] == "ARTIFACT_PUBLISHED"
    )
    assert [events[index]["event_type"] for index in input_positions] == [
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",
    ]
    assert input_positions[-1] < ownership_publish

    before_events = derive_state(run_root)["events"]
    before_bytes = (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes()
    assert _materialize(tmp_path, inputs) == delta
    assert derive_state(run_root)["events"] == before_events
    assert (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes() == before_bytes

    conflicting = json.loads(json.dumps(review))
    conflicting["warnings"] = ["different reviewed carrier"]
    # A carrier that differs from the durable review aggregate is refused before the
    # frozen execution inputs can be touched.
    with pytest.raises(GeneratedDeltaError, match="execution inputs|materialization|accepted reviewed automation chain is invalid"):
        _materialize(tmp_path, (inputs[0], automation, conflicting, inputs[3], run_root, attempt_id))
    assert (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes() == before_bytes


def test_materialization_rejects_missing_automation_model_lifecycle_before_side_effects(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError
    from tools.pilot_state import derive_state

    inputs = _inputs(tmp_path, automation_lifecycle=False)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs

    # Without the durable per-part review the chain stops at its first missing link.
    with pytest.raises(GeneratedDeltaError, match="execution inputs|static review boundary is not durable/read-back"):
        _materialize(tmp_path, inputs)

    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert not (run_root / "execution-inputs" / f"{attempt_id}.json").exists()
    assert not (run_root / "materialization-ownership" / f"{attempt_id}.json").exists()
    assert not (run_root / "generated-deltas" / f"{attempt_id}.json").exists()
    assert "ATTEMPT_TERMINAL" not in {
        event["event_type"]
        for event in derive_state(run_root)["events"]
        if event.get("attempt_id") == attempt_id
    }


def test_execution_inputs_readback_interruption_recovers_before_any_project_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools import pilot_state

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    original_append = pilot_state.append_event

    def interrupt_readback(root: Path, event_type: str, **kwargs):
        slot = Path(root) / "execution-inputs" / f"{attempt_id}.json"
        if event_type == "ARTIFACT_READ_BACK" and slot.exists():
            receipt = json.loads(slot.read_text(encoding="utf-8"))
            if kwargs.get("artifact_digest") == receipt.get("digest"):
                raise RuntimeError("interrupt execution-input readback")
        return original_append(root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", interrupt_readback)
    with pytest.raises(RuntimeError, match="execution-input readback"):
        _materialize(tmp_path, inputs)
    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert not (run_root / "materialization-ownership" / f"{attempt_id}.json").exists()

    installed = (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes()
    monkeypatch.setattr(pilot_state, "append_event", original_append)
    conflicting_review = json.loads(json.dumps(inputs[2]))
    conflicting_review["warnings"] = ["caller carrier must not replace installed bytes"]
    with pytest.raises(ValueError, match="conflicting execution inputs"):
        pilot_state.publish_execution_inputs(
            run_root, attempt_id, inputs[1], conflicting_review,
        )
    durable = pilot_state.read_execution_inputs(run_root, attempt_id)
    assert durable["automation_artifact"] == inputs[1]
    assert durable["autotest_review"] == inputs[2]
    assert (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes() == installed
    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert not (run_root / "materialization-ownership" / f"{attempt_id}.json").exists()

    delta = _materialize(tmp_path, inputs)
    assert delta["facts"]["completion"] == "COMPLETE"
    assert (run_root / "execution-inputs" / f"{attempt_id}.json").read_bytes() == installed


def test_execution_inputs_recovery_is_forbidden_after_execution_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools import pilot_state

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    original_append = pilot_state.append_event

    def interrupt_readback(root: Path, event_type: str, **kwargs):
        slot = Path(root) / "execution-inputs" / f"{attempt_id}.json"
        if event_type == "ARTIFACT_READ_BACK" and slot.exists():
            receipt = json.loads(slot.read_text(encoding="utf-8"))
            if kwargs.get("artifact_digest") == receipt.get("digest"):
                raise RuntimeError("interrupt execution-input readback")
        return original_append(root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", interrupt_readback)
    with pytest.raises(RuntimeError, match="execution-input readback"):
        _materialize(tmp_path, inputs)
    monkeypatch.setattr(pilot_state, "append_event", original_append)
    pilot_state.claim_execution_start(run_root, attempt_id, "sha256:" + "9" * 64)

    with pytest.raises(ValueError, match="execution-inputs frontier"):
        pilot_state.read_execution_inputs(run_root, attempt_id)


def test_existing_even_byte_identical_unowned_path_is_never_overwritten(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, materialize_delta

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    path = tmp_path / "tests" / "test_products.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(automation["artifacts"]["generated_files"][0]["content"], encoding="utf-8")
    try:
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    except GeneratedDeltaError as error:
        assert error.code == "MATERIALIZATION_CONFLICT"
    else:
        raise AssertionError("unowned existing file was accepted")


def test_ownership_receipt_precedes_writes_and_exact_retry_recovers_orphans(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError
    from tools.pilot_state import read_attempt_receipt
    from tools.schema_validation import schema_diagnostics

    inputs = _inputs(tmp_path, mutate=_add_second_file)
    baseline, automation, review, _document, run_root, attempt_id = inputs
    with pytest.raises(GeneratedDeltaError) as interrupted:
        _materialize(tmp_path, inputs, interrupt_after=1)
    assert interrupted.value.code == "MATERIALIZATION_INTERRUPTED"
    ownership = read_attempt_receipt(run_root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert set(ownership) == {"schema_version", "stage", "module", "test_root", "baseline_digest", "automation_digest", "review_digest", "effective_canonical_digest", "effective_bundle_receipt_digest", "files"}
    assert ownership["stage"] == "materialization-ownership"
    assert all(set(row) == {"file_id", "path", "content_digest", "baseline_absent", "ownership_digest"} for row in ownership["files"])
    assert schema_diagnostics(ownership, Path("schemas/materialization-receipt.schema.json"), Path.cwd()) == []
    with pytest.raises(ValueError, match="missing generated-delta"):
        read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
    first = tmp_path / "tests" / "test_products.py"
    second = tmp_path / "tests" / "test_second.py"
    first_bytes, first_mtime = first.read_bytes(), first.stat().st_mtime_ns
    assert not second.exists()

    delta = _materialize(tmp_path, inputs)
    assert delta["facts"]["completion"] == "COMPLETE"
    assert first.read_bytes() == first_bytes
    assert first.stat().st_mtime_ns == first_mtime
    assert second.exists()
    assert ownership["baseline_digest"] == baseline["digest"]
    assert ownership["automation_digest"] == delta["automation_digest"]
    assert ownership["review_digest"] == delta["review_digest"]


def test_owned_orphan_drift_is_preserved_and_retry_conflicts(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError

    inputs = _inputs(tmp_path, mutate=_add_second_file)
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        _materialize(tmp_path, inputs, interrupt_after=1)
    first = tmp_path / "tests" / "test_products.py"
    first.write_bytes(b"user drift")

    with pytest.raises(GeneratedDeltaError) as conflict:
        _materialize(tmp_path, inputs)
    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert first.read_bytes() == b"user drift"
    assert not (tmp_path / "tests" / "test_second.py").exists()


def test_new_ownership_never_adopts_a_post_preflight_exact_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import GeneratedDeltaError
    from tools.pilot_state import read_attempt_receipt

    inputs = _inputs(tmp_path)
    original = generated_delta.create_confined_bytes_exclusive

    def create_after_race(project, root, target, data, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return original(project, root, target, data, **kwargs)

    monkeypatch.setattr(generated_delta, "create_confined_bytes_exclusive", create_after_race)
    with pytest.raises(GeneratedDeltaError) as conflict:
        _materialize(tmp_path, inputs)
    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert (tmp_path / "tests" / "test_products.py").read_bytes() == inputs[1]["artifacts"]["generated_files"][0]["content"].encode()
    assert read_attempt_receipt(inputs[4], inputs[5], "materialization-ownership", "ARTIFACT_READ_BACK")["record"]["payload"]["stage"] == "materialization-ownership"


def test_ownership_publication_failure_performs_zero_project_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.pilot_state as pilot_state
    from tools.generated_delta import GeneratedDeltaError

    inputs = _inputs(tmp_path)

    def publication_failure(*_args, **_kwargs):
        raise OSError("injected ownership publication failure")

    monkeypatch.setattr(pilot_state, "publish_phase5_receipt", publication_failure)
    with pytest.raises(GeneratedDeltaError) as error:
        _materialize(tmp_path, inputs)
    assert error.value.code == "MATERIALIZATION_CONFLICT"
    assert not (tmp_path / "tests" / "test_products.py").exists()


def test_existing_ownership_must_canonically_match_requested_plan(tmp_path: Path):
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.generated_delta import GeneratedDeltaError, _file_row
    from tools.pilot_state import publish_execution_inputs, publish_phase5_receipt

    baseline, automation, _review, _document, run_root, attempt_id = _inputs(tmp_path)
    from tools.pilot_state import read_effective_canonical
    effective = read_effective_canonical(run_root, attempt_id)
    generated = automation["artifacts"]["generated_files"][0]
    content = generated["content"].encode("utf-8")
    automation_digest = automation_sha256(automation)
    review_digest = autotest_review_sha256(_review)
    publish_execution_inputs(run_root, attempt_id, automation, _review)
    file_id, path = "FILE-different", "tests/test_different.py"
    row = _file_row(file_id, path, content, baseline, automation_digest, review_digest, "MATERIALIZED")
    plan = {
        "schema_version": "1.0.0", "stage": "materialization-ownership", "module": ".", "test_root": baseline["test_root"],
        "baseline_digest": baseline["digest"], "automation_digest": automation_digest, "review_digest": review_digest,
        "effective_canonical_digest": effective["document_digest"], "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
        "files": [{key: row[key] for key in ("file_id", "path", "content_digest", "baseline_absent", "ownership_digest")}],
    }
    assert publish_phase5_receipt(run_root, attempt_id, "materialization-ownership", plan)["created"] is True

    with pytest.raises(GeneratedDeltaError) as conflict:
        _materialize(tmp_path, (baseline, automation, _review, _document, run_root, attempt_id))
    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert not (tmp_path / "tests" / "test_products.py").exists()


def test_delta_is_closed_schema_valid_and_unknown_never_cleans(tmp_path: Path):
    from tools.generated_delta import apply_dispositions, inspect_delta
    from tools.pilot_state import read_attempt_receipt
    from tools.schema_validation import schema_diagnostics

    inputs = _inputs(tmp_path)
    baseline, automation, review, document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    assert schema_diagnostics(delta, Path("schemas/generated-delta.schema.json"), Path.cwd()) == []
    evidence = "sha256:" + "a" * 64
    result = apply_dispositions(tmp_path, delta, {"tests/test_products.py": "PRESERVED_EXECUTION_UNKNOWN"}, run_root=run_root, attempt_id=attempt_id, verification="UNKNOWN", execution_unknown_evidence_digest=evidence)
    assert result["files"][0]["disposition"] == "PRESERVED_EXECUTION_UNKNOWN"
    assert (tmp_path / "tests" / "test_products.py").exists()
    assert inspect_delta(tmp_path, result) == {"valid": True}
    receipt = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert receipt["generated_delta_digest"] == delta["digest"]
    assert receipt["verification"] == "UNKNOWN"
    assert receipt["execution_unknown_evidence_digest"] == evidence
    assert receipt["files"][0]["disposition"] == "PRESERVED_EXECUTION_UNKNOWN"


def test_complete_not_applicable_cleanup_is_rejected_before_plan_or_effect(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    generated = tmp_path / delta["files"][0]["path"]

    with pytest.raises(GeneratedDeltaError) as conflict:
        apply_dispositions(
            tmp_path, delta, {delta["files"][0]["path"]: "CLEANED"},
            run_root=run_root, attempt_id=attempt_id, verification="NOT_APPLICABLE",
        )

    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert generated.is_file()
    assert not (run_root / "disposition-plans" / f"{attempt_id}.json").exists()
    assert not (run_root / "disposition-receipts" / f"{attempt_id}.json").exists()


def test_execution_derived_cleanup_rejects_missing_or_mismatched_execution_receipt(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    generated = tmp_path / "tests" / "test_products.py"

    with pytest.raises(GeneratedDeltaError) as conflict:
        apply_dispositions(
            tmp_path,
            delta,
            {"tests/test_products.py": "CLEANED"},
            run_root=run_root,
            attempt_id=attempt_id,
            verification="FAIL",
        )

    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert generated.is_file()
    assert not (run_root / "disposition-plans" / f"{attempt_id}.json").exists()
    assert not (run_root / "disposition-receipts" / f"{attempt_id}.json").exists()

    mismatch_project = tmp_path / "mismatch"
    mismatch_project.mkdir()
    delta, run_root, attempt_id = _published_execution_delta(mismatch_project, failed=False)
    generated = mismatch_project / delta["files"][0]["path"]

    with pytest.raises(GeneratedDeltaError) as conflict:
        apply_dispositions(
            mismatch_project, delta, {delta["files"][0]["path"]: "CLEANED"},
            run_root=run_root, attempt_id=attempt_id, verification="FAIL",
        )

    assert conflict.value.code == "MATERIALIZATION_CONFLICT"
    assert generated.is_file()
    assert not (run_root / "disposition-plans" / f"{attempt_id}.json").exists()


def test_disposition_requires_a_decision_for_every_generated_file(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    inputs = _inputs(tmp_path)
    baseline, automation, review, document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)

    try:
        apply_dispositions(tmp_path, delta, {}, run_root=run_root, attempt_id=attempt_id, verification="PASS")
    except GeneratedDeltaError as error:
        assert error.code == "MATERIALIZATION_CONFLICT"
    else:
        raise AssertionError("a complete generated delta accepted an incomplete disposition")


def test_disposition_recovery_publishes_plan_before_cleanup_and_recovers_deleted_file(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions
    from tools.pilot_state import read_attempt_receipt
    from tools.schema_validation import schema_diagnostics

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    path = tmp_path / relative_path
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        apply_dispositions(
            tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id,
            verification="FAIL", interrupt_after_effects=1,
        )
    assert not path.exists()
    plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert plan["stage"] == "disposition-plan"
    assert schema_diagnostics(plan, Path("schemas/disposition-receipt.schema.json"), Path.cwd()) == []

    recovered = apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    assert recovered["files"][0]["disposition"] == "CLEANED"
    final = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert final["stage"] == "dispositions"
    assert final["disposition_plan_digest"] == plan["digest"]


def test_disposition_plan_recovers_when_persisted_before_its_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.pilot_state as pilot_state
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions
    from tools.pilot_state import derive_state, read_attempt_receipt

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    original_append = pilot_state.append_event

    def fail_plan_event(*args, **kwargs):
        if len(args) > 1 and args[1] == "ARTIFACT_PUBLISHED":
            raise OSError("injected plan event failure")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", fail_plan_event)
    with pytest.raises(GeneratedDeltaError, match="plan publication is uncertain"):
        apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    raw_path = run_root / "disposition-plans" / f"{attempt_id}.json"
    assert raw_path.exists()

    monkeypatch.setattr(pilot_state, "append_event", original_append)
    result = apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")
    events = [
        event["event_type"] for event in derive_state(run_root)["events"]
        if event.get("attempt_id") == attempt_id and event.get("artifact_digest") == plan["digest"]
    ]
    assert events == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
    assert result["files"][0]["disposition"] == "CLEANED"
    with pytest.raises(GeneratedDeltaError, match="disposition replay facts conflict"):
        apply_dispositions(tmp_path, delta, {relative_path: "RETAINED"}, run_root=run_root, attempt_id=attempt_id, verification="PASS")


def test_conflicting_raw_disposition_plan_remains_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.pilot_state as pilot_state
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    original_append = pilot_state.append_event

    def fail_plan_event(*args, **kwargs):
        if len(args) > 1 and args[1] == "ARTIFACT_PUBLISHED":
            raise OSError("injected plan event failure")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", fail_plan_event)
    with pytest.raises(GeneratedDeltaError, match="plan publication is uncertain"):
        apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    (run_root / "disposition-plans" / f"{attempt_id}.json").write_bytes(b"{}")
    monkeypatch.setattr(pilot_state, "append_event", original_append)
    with pytest.raises(GeneratedDeltaError, match="disposition plan is invalid or unbound"):
        apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")


def test_cleanup_missing_before_plan_is_not_recovered_as_cleaned(tmp_path: Path):
    from tools.generated_delta import apply_dispositions
    from tools.pilot_state import read_attempt_receipt

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    (tmp_path / relative_path).unlink()
    result = apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert plan["files"][0]["pre_effect_state"] == "MISSING"
    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"


def test_cleanup_exact_plan_crash_then_missing_is_recovered_cleaned(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions
    from tools.pilot_state import read_attempt_receipt

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL", interrupt_after_plan=True)
    assert read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]["files"][0]["pre_effect_state"] == "EXACT"
    (tmp_path / relative_path).unlink()
    result = apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    assert result["files"][0]["disposition"] == "CLEANED"


def test_preplan_drift_is_never_cleaned_after_byte_identical_restore(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    relative_path = delta["files"][0]["path"]
    path = tmp_path / relative_path
    exact_bytes = path.read_bytes()
    path.write_bytes(b"pre-plan drift")
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL", interrupt_after_plan=True)
    path.write_bytes(exact_bytes)
    result = apply_dispositions(tmp_path, delta, {relative_path: "CLEANED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert path.exists()


@pytest.mark.parametrize(
    ("verification", "requested_disposition", "evidence"),
    [
        ("PASS", "RETAINED", None),
        ("UNKNOWN", "PRESERVED_EXECUTION_UNKNOWN", "sha256:" + "f" * 64),
    ],
)
@pytest.mark.parametrize("pre_plan_state", ["missing", "drift"])
def test_preplan_conflict_never_becomes_accepted_pass_or_unknown_disposition(
    tmp_path: Path, verification: str, requested_disposition: str, evidence: str | None, pre_plan_state: str,
):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions
    from tools.pilot_state import read_attempt_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    path = tmp_path / "tests" / "test_products.py"
    exact_bytes = path.read_bytes()
    if pre_plan_state == "missing":
        path.unlink()
    else:
        path.write_bytes(b"pre-plan drift")
    kwargs = {"execution_unknown_evidence_digest": evidence} if evidence is not None else {}
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        apply_dispositions(
            tmp_path, delta, {"tests/test_products.py": requested_disposition}, run_root=run_root, attempt_id=attempt_id,
            verification=verification, interrupt_after_plan=True, **kwargs,
        )
    plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert plan["files"][0]["pre_effect_state"] == {"missing": "MISSING", "drift": "DRIFT"}[pre_plan_state]
    path.write_bytes(exact_bytes)
    assert _digest(path.read_bytes()) == delta["files"][0]["content_digest"]
    result = apply_dispositions(
        tmp_path, delta, {"tests/test_products.py": requested_disposition}, run_root=run_root, attempt_id=attempt_id,
        verification=verification, **kwargs,
    )
    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert result["files"][0]["disposition"] != requested_disposition
    assert path.exists()


def test_unknown_disposition_binds_evidence_and_never_removes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import apply_dispositions
    from tools.pilot_state import read_attempt_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    remove_calls = 0

    def no_remove(*_args, **_kwargs):
        nonlocal remove_calls
        remove_calls += 1
        raise AssertionError("UNKNOWN must not remove a generated file")

    monkeypatch.setattr(generated_delta, "remove_confined_bytes_if_equal", no_remove)
    evidence = "sha256:" + "e" * 64
    result = apply_dispositions(
        tmp_path, delta, {"tests/test_products.py": "PRESERVED_EXECUTION_UNKNOWN"}, run_root=run_root, attempt_id=attempt_id,
        verification="UNKNOWN", execution_unknown_evidence_digest=evidence,
    )
    assert result["files"][0]["disposition"] == "PRESERVED_EXECUTION_UNKNOWN"
    assert remove_calls == 0
    final = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert final["execution_unknown_evidence_digest"] == evidence


def test_exact_final_disposition_replay_does_not_mutate_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import apply_dispositions

    delta, run_root, attempt_id = _published_execution_delta(tmp_path, failed=True)
    requested = {delta["files"][0]["path"]: "CLEANED"}
    first = apply_dispositions(tmp_path, delta, requested, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    assert first["files"][0]["disposition"] == "CLEANED"

    def mutation_is_forbidden(*_args, **_kwargs):
        raise AssertionError("exact final replay attempted a filesystem mutation")

    monkeypatch.setattr(generated_delta, "remove_confined_bytes_if_equal", mutation_is_forbidden)
    replay = apply_dispositions(tmp_path, delta, requested, run_root=run_root, attempt_id=attempt_id, verification="FAIL")
    assert replay == first


@pytest.mark.parametrize("state", ["drift", "missing"])
def test_unknown_disposition_inspects_bytes_without_cleanup(tmp_path: Path, state: str, monkeypatch: pytest.MonkeyPatch):
    import tools.generated_delta as generated_delta
    from tools.generated_delta import apply_dispositions

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    path = tmp_path / "tests" / "test_products.py"
    if state == "drift":
        path.write_bytes(b"user drift")
    else:
        path.unlink()

    def no_remove(*_args, **_kwargs):
        raise AssertionError("UNKNOWN must not remove a generated file")

    monkeypatch.setattr(generated_delta, "remove_confined_bytes_if_equal", no_remove)
    result = apply_dispositions(
        tmp_path, delta, {"tests/test_products.py": "PRESERVED_EXECUTION_UNKNOWN"}, run_root=run_root, attempt_id=attempt_id,
        verification="UNKNOWN", execution_unknown_evidence_digest="sha256:" + "b" * 64,
    )
    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert result["files"][0]["reason_code"] == "CONTENT_CONFLICT"


def test_disposition_final_replay_rejects_different_facts(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    evidence = "sha256:" + "c" * 64
    apply_dispositions(
        tmp_path, delta, {"tests/test_products.py": "PRESERVED_EXECUTION_UNKNOWN"}, run_root=run_root, attempt_id=attempt_id,
        verification="UNKNOWN", execution_unknown_evidence_digest=evidence,
    )
    with pytest.raises(GeneratedDeltaError) as mismatch:
        apply_dispositions(
            tmp_path, delta, {"tests/test_products.py": "PRESERVED_EXECUTION_UNKNOWN"}, run_root=run_root, attempt_id=attempt_id,
            verification="UNKNOWN", execution_unknown_evidence_digest="sha256:" + "d" * 64,
        )
    assert mismatch.value.code == "MATERIALIZATION_CONFLICT"
    with pytest.raises(GeneratedDeltaError) as verification_mismatch:
        apply_dispositions(tmp_path, delta, {"tests/test_products.py": "RETAINED"}, run_root=run_root, attempt_id=attempt_id, verification="PASS")
    assert verification_mismatch.value.code == "MATERIALIZATION_CONFLICT"


def test_pilot_rejects_forged_or_wrong_attempt_disposition_payload(tmp_path: Path):
    from tools.pilot_state import publish_phase5_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    forged = {"schema_version": "1.0.0", "stage": "disposition-plan"}
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", forged)
    with pytest.raises(ValueError):
        publish_phase5_receipt(run_root, "0" * 32, "disposition-plan", forged)


def test_disposition_publisher_rejects_duplicate_or_subset_plan(tmp_path: Path):
    from tools.pilot_state import _sealed, publish_phase5_receipt

    inputs = _inputs(tmp_path, mutate=_add_second_file)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    row = delta["files"][0]
    plan_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"operation": "RETAIN_IF_EXACT", "requested_disposition": "RETAINED", "pre_effect_state": "EXACT"}
    duplicate_plan = _sealed({
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS",
        "files": [plan_row, {**plan_row, "file_id": "FILE-case-collision", "path": "tests/TEST_PRODUCTS.py"}],
    })
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", duplicate_plan)

    subset_plan = _sealed({
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS",
        "files": [plan_row],
    })
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", subset_plan)


def test_disposition_publisher_rejects_wrong_plan_relation(tmp_path: Path):
    from tools.pilot_state import _sealed, publish_phase5_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    row = delta["files"][0]
    plan_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"operation": "RETAIN_IF_EXACT", "requested_disposition": "CLEANED", "pre_effect_state": "EXACT"}
    wrong_operation = _sealed({
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [plan_row],
    })
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", wrong_operation)


def test_disposition_publisher_rejects_wrong_delta_stage_and_final_relation(tmp_path: Path):
    from tools.pilot_state import _sealed, publish_phase5_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    row = delta["files"][0]
    plan_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"operation": "RETAIN_IF_EXACT", "requested_disposition": "RETAINED", "pre_effect_state": "EXACT"}
    wrong_delta = _sealed({
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": "sha256:" + "f" * 64, "verification": "PASS", "files": [plan_row],
    })
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", wrong_delta)
    valid_plan = _sealed({
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [plan_row],
    })
    publish_phase5_receipt(run_root, attempt_id, "disposition-plan", valid_plan)
    forged_final_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"disposition": "CLEANED"}
    forged_final = _sealed({
        "schema_version": "1.0.0", "stage": "dispositions", "disposition_plan_digest": "sha256:" + "e" * 64,
        "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [forged_final_row],
    })
    with pytest.raises(ValueError, match="invalid disposition-receipt receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-receipt", forged_final)
    stage_swapped = _sealed({
        "schema_version": "1.0.0", "stage": "dispositions", "generated_delta_digest": delta["digest"], "verification": "PASS", "files": [plan_row],
    })
    with pytest.raises(ValueError, match="invalid disposition-plan receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-plan", stage_swapped)


@pytest.mark.parametrize(
    ("verification", "operation", "requested_disposition", "pre_effect_state", "disposition", "reason_code", "evidence"),
    [
        ("PASS", "RETAIN_IF_EXACT", "RETAINED", "MISSING", "RETAINED", None, None),
        ("FAIL", "CLEAN_IF_EXACT", "CLEANED", "DRIFT", "CLEANED", None, None),
        ("UNKNOWN", "PRESERVE_UNKNOWN", "PRESERVED_EXECUTION_UNKNOWN", "UNSAFE", "PRESERVED_EXECUTION_UNKNOWN", "EXECUTION_UNKNOWN", "sha256:" + "a" * 64),
    ],
)
def test_disposition_publisher_rejects_accepted_final_for_nonexact_plan_state(
    tmp_path: Path, verification: str, operation: str, requested_disposition: str, pre_effect_state: str,
    disposition: str, reason_code: str | None, evidence: str | None,
):
    from tools.pilot_state import _sealed, publish_phase5_receipt

    inputs = _inputs(tmp_path)
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    delta = _materialize(tmp_path, inputs)
    row = delta["files"][0]
    plan_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"operation": operation, "requested_disposition": requested_disposition, "pre_effect_state": pre_effect_state}
    plan_body = {
        "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": delta["digest"],
        "verification": verification, "files": [plan_row],
    }
    if evidence is not None:
        plan_body["execution_unknown_evidence_digest"] = evidence
    plan = _sealed(plan_body)
    publish_phase5_receipt(run_root, attempt_id, "disposition-plan", plan)
    final_row = {
        key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
    } | {"disposition": disposition}
    if reason_code is not None:
        final_row["reason_code"] = reason_code
    final_body = {
        "schema_version": "1.0.0", "stage": "dispositions", "disposition_plan_digest": plan["digest"],
        "generated_delta_digest": delta["digest"], "verification": verification, "files": [final_row],
    }
    if evidence is not None:
        final_body["execution_unknown_evidence_digest"] = evidence
    with pytest.raises(ValueError, match="invalid disposition-receipt receipt"):
        publish_phase5_receipt(run_root, attempt_id, "disposition-receipt", _sealed(final_body))


@pytest.mark.parametrize("reverse", [False, True])
def test_component_prefix_generated_paths_fail_before_ownership_or_project_write(tmp_path: Path, reverse: bool):
    from tools.generated_delta import GeneratedDeltaError
    from tools.pilot_state import read_attempt_receipt

    inputs = _inputs(tmp_path, mutate=lambda automation: _add_component_prefix_collision(automation, reverse=reverse))
    _baseline, _automation, _review, _document, run_root, attempt_id = inputs
    with pytest.raises(GeneratedDeltaError, match="component-colliding"):
        _materialize(tmp_path, inputs)
    assert not (tmp_path / "tests" / "test_products.py").exists()
    with pytest.raises(ValueError):
        read_attempt_receipt(run_root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK")


def test_materialization_requires_preexisting_authoritative_test_root(tmp_path: Path):
    from tools.generated_delta import GeneratedDeltaError, materialize_delta

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    (tmp_path / "tests").rmdir()
    with pytest.raises(GeneratedDeltaError, match="test root"):
        materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)


def test_case_collision_and_escape_are_rejected_before_any_write(tmp_path: Path):
    from tools.automation_validation import validate_automation_artifact
    from tools.generated_delta import GeneratedDeltaError

    def add_casefolding_collision(automation: dict) -> None:
        artifacts = automation["artifacts"]
        artifacts["generated_files"].append({
            **artifacts["generated_files"][0],
            "file_id": "FILE-other",
            "path": "tests/TEST_PRODUCTS.py",
        })
        artifacts["generated_symbols"].append({
            "file_id": "FILE-other",
            "symbol_id": "SYMBOL-other",
            "locator": {"kind": "python_module_function", "function_name": "test_other"},
        })
        relations = artifacts["implementation_relations"]
        artifacts["implementation_relations"] = [
            {**relations[0], "file_id": "FILE-other", "symbol_id": "SYMBOL-other"}, relations[0],
            {**relations[1], "file_id": "FILE-other", "symbol_id": "SYMBOL-other"}, relations[1],
        ]

    refused = "accepted reviewed automation chain is invalid|static review boundary is not durable/read-back"
    # The bounded review refuses to snapshot an invalid artifact, so it can never be reviewed...
    with pytest.raises(ValueError, match="automation review snapshot is unbound"):
        _inputs(tmp_path, mutate=add_casefolding_collision)
    # ...and materialization refuses it as well when it arrives without that review.
    inputs = _inputs(tmp_path, mutate=add_casefolding_collision, automation_lifecycle=False)
    _baseline, automation, _review, document, _run_root, _attempt_id = inputs
    assert {row["code"] for row in validate_automation_artifact(automation, document)} == {"AUTOMATION_DUPLICATE_PATH"}
    with pytest.raises(GeneratedDeltaError, match=refused) as collision:
        _materialize(tmp_path, inputs)
    assert collision.value.code == "MATERIALIZATION_CONFLICT"
    assert not (tmp_path / "tests" / "test_products.py").exists()
    inputs = _inputs(tmp_path, mutate=lambda automation: automation["artifacts"]["generated_files"][0].__setitem__("path", "tests/../escape.py"), automation_lifecycle=False)
    _baseline, automation, _review, document, _run_root, _attempt_id = inputs
    assert {row["code"] for row in validate_automation_artifact(automation, document)} == {"AUTOMATION_PORTABLE_PATH"}
    with pytest.raises(GeneratedDeltaError, match=refused) as escape:
        _materialize(tmp_path, inputs)
    assert escape.value.code == "MATERIALIZATION_CONFLICT"
    assert not (tmp_path / "escape.py").exists()


def test_generated_delta_schema_rejects_unknown_facts_verification(tmp_path: Path):
    delta = _materialize(tmp_path, _inputs(tmp_path))
    delta["facts"]["verification"] = "UNSUPPORTED"
    schema = json.loads(Path("schemas/generated-delta.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(delta)


def test_generated_delta_schema_rejects_dispositions_and_invalid_completion_shapes(tmp_path: Path):
    complete = _materialize(tmp_path, _inputs(tmp_path))
    schema = json.loads(Path("schemas/generated-delta.schema.json").read_text(encoding="utf-8"))
    embedded_disposition = json.loads(json.dumps(complete))
    embedded_disposition["files"][0]["disposition"] = "CLEANED"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(embedded_disposition)
    partial_without_unwritten = json.loads(json.dumps(complete))
    partial_without_unwritten["facts"] = {"completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(partial_without_unwritten)
