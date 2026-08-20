#!/usr/bin/env python3
"""Validate the authoritative closed Pipeline 6.0 contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

if __package__:
    from .json_cli import JsonArgumentParser
else:
    from json_cli import JsonArgumentParser


CORE_SKILLS = ["change-scope", "context-marker", "test-classifier", "test-classifier-reviewer", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
SKILL_FILES = {name: f"skills/{name}/SKILL.md" for name in CORE_SKILLS}
ARTIFACT_IDS = ["raw_content", "technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt", "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting", "behavior_context_receipt", "technical_test_classification", "classification_review", "effective_technical_evidence", "canonical_document_delta", "delta_application_receipt", "unchanged_document_selection", "candidate_document", "candidate_bundle_receipt", "validation_report", "successor_document", "successor_bundle_receipt", "effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review", "run_result", "trace_document", "trace_audit", "orchestrator_output", "terminal_run_receipt", "baseline_advancement", "successor_baseline_receipt"]
VERDICTS = {"classification": ["ПРИНЯТО", "ТРЕБУЕТ ДОРАБОТКИ"], "review": ["ПРИНЯТО", "AUTO_FIX_APPLIED", "ТРЕБУЕТ ДОРАБОТКИ"], "execution": ["PASS", "FAIL", "NOT_RUNNABLE"], "trace": ["PASS", "FAIL"]}
HISTORICAL_REJECTIONS = [{"component": "pipeline", "version": "5.0", "live_status": "REJECTED"}, {"component": "context-marker", "version": "5.0.0", "live_status": "REJECTED"}, {"component": "behavior-context-receipt", "version": "1.0.0", "live_status": "REJECTED"}]
STAGE_LAYOUT = [
    ("source-inventory", "tool", ["raw_content"], ["raw_content"], ["technical_test_inventory", "authorized_behavior_sources"], None),
    ("change-scope", "skill", ["raw_content", "technical_test_inventory", "authorized_behavior_sources"], ["raw_content", "technical_test_inventory", "authorized_behavior_sources"], ["change_scope_receipt"], None),
    ("context-marker", "skill", ["raw_content", "technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt"], ["technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt"], ["managed_behavior_context", "changed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"], None),
    ("test-classifier", "skill", ["technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt", "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"], ["technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt", "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"], ["technical_test_classification"], None),
    ("test-classifier-reviewer", "skill", ["technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt", "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting", "behavior_context_receipt", "technical_test_classification"], ["changed_behavior_context"], ["classification_review", "effective_technical_evidence"], None),
    ("tc-generator", "skill", ["changed_behavior_context"], ["changed_behavior_context"], ["candidate_document", "canonical_document_delta"], [{"when": {"run_mode": "FULL"}, "produces": ["candidate_document"]}, {"when": {"run_mode": "CHANGE_SET"}, "produces": ["canonical_document_delta"]}]),
    ("apply-document-delta", "tool", ["changed_behavior_context", "canonical_document_delta"], [], ["delta_application_receipt", "candidate_document", "unchanged_document_selection"], [{"when": {"delta_status": "CHANGED"}, "produces": ["delta_application_receipt", "candidate_document"]}, {"when": {"delta_status": "UNCHANGED"}, "produces": ["delta_application_receipt", "unchanged_document_selection"]}]),
    ("select-unchanged-document", "tool", ["delta_application_receipt", "unchanged_document_selection"], [], ["effective_document", "effective_bundle_receipt"], None),
    ("publish-candidate", "tool", ["candidate_document"], ["candidate_document"], ["candidate_bundle_receipt"], None),
    ("tc-reviewer", "skill", ["candidate_document"], ["candidate_document"], ["validation_report", "successor_document"], None),
    ("revision-orchestrator", "tool", ["candidate_document", "candidate_bundle_receipt", "validation_report", "successor_document"], ["candidate_bundle_receipt", "validation_report"], ["successor_bundle_receipt", "effective_document", "effective_bundle_receipt"], None),
    ("tc-to-autotest", "skill", ["effective_document", "effective_bundle_receipt"], ["effective_document", "effective_bundle_receipt"], ["automation_artifact"], None),
    ("autotest-reviewer", "skill", ["effective_document", "automation_artifact"], ["effective_document", "automation_artifact"], ["autotest_review"], None),
    ("run-tests", "tool", ["effective_document", "automation_artifact", "autotest_review"], ["effective_document", "automation_artifact", "autotest_review"], ["run_result"], None),
    ("build-trace", "tool", ["effective_document", "automation_artifact", "run_result"], ["effective_document", "automation_artifact", "run_result"], ["trace_document"], None),
    ("trace-check", "tool", ["trace_document"], ["trace_document"], ["trace_audit"], None),
    ("finalize-orchestration", "tool", ["effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review", "run_result", "trace_document", "trace_audit"], [], ["orchestrator_output", "terminal_run_receipt"], None),
    ("advance-baseline", "tool", ["terminal_run_receipt"], [], ["baseline_advancement", "successor_baseline_receipt"], [{"when": {"baseline_status": "eligible"}, "produces": ["baseline_advancement", "successor_baseline_receipt"]}, {"when": {"baseline_status": "ineligible"}, "produces": ["baseline_advancement"]}]),
]
TRANSITIONS = [
    {"from": "test-classifier-reviewer", "when": {"classification_verdict": "ПРИНЯТО"}, "transform": "continue_isolated_technical_evidence_gate"}, {"from": "test-classifier-reviewer", "when": {"classification_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, "transform": "stop_classification_rework"},
    {"from": "tc-generator", "when": {"run_mode": "FULL"}, "transform": "publish_candidate"}, {"from": "tc-generator", "when": {"run_mode": "CHANGE_SET"}, "transform": "apply_document_delta"}, {"from": "apply-document-delta", "when": {"delta_status": "CHANGED"}, "transform": "publish_candidate"}, {"from": "apply-document-delta", "when": {"delta_status": "UNCHANGED"}, "transform": "select_unchanged_document"},
    {"from": "tc-reviewer", "when": {"review_verdict": "ПРИНЯТО"}, "transform": "revision_orchestrator_selects_candidate"}, {"from": "tc-reviewer", "when": {"review_verdict": "AUTO_FIX_APPLIED"}, "transform": "revision_orchestrator_validates_publishes_selects_successor"}, {"from": "tc-reviewer", "when": {"review_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, "transform": "stop_rework"},
    {"from": "autotest-reviewer", "when": {"review_verdict": "ПРИНЯТО", "automation_status": "BLOCKED"}, "transform": "build_trace_without_run"}, {"from": "autotest-reviewer", "when": {"review_verdict": "ПРИНЯТО", "required_symbol_pairs": 0}, "transform": "build_trace_without_run"}, {"from": "autotest-reviewer", "when": {"review_verdict": "ПРИНЯТО", "required_symbol_pairs": "one_or_more"}, "transform": "run_tests"}, {"from": "autotest-reviewer", "when": {"review_verdict": "AUTO_FIX_APPLIED"}, "transform": "regenerate_automation_and_review_again"}, {"from": "autotest-reviewer", "when": {"review_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, "transform": "stop_rework"},
    {"from": "run-tests", "when": {"execution_verdict": "PASS"}, "transform": "build_trace_then_trace_check"}, {"from": "run-tests", "when": {"execution_verdict": "FAIL"}, "transform": "build_trace_then_trace_check"}, {"from": "run-tests", "when": {"execution_verdict": "NOT_RUNNABLE"}, "transform": "build_trace_then_trace_check"}, {"from": "trace-check", "when": {"trace_verdict": "PASS"}, "transform": "finalize_orchestration_then_advance_baseline"}, {"from": "trace-check", "when": {"trace_verdict": "FAIL"}, "transform": "stop_invalid_trace"},
]
RUNTIME_FILES = (
    "tools/assertion_dsl.py", "tools/automation_validation.py", "tools/baseline_lifecycle.py",
    "tools/batch_promotion.py", "tools/behavior_context_planning.py", "tools/build_trace_document.py",
    "tools/canonical_document.py", "tools/change_scope.py", "tools/contract_check.py",
    "tools/discover_project.py", "tools/document_delta.py", "tools/execution_preflight.py", "tools/feature_flow.py",
    "tools/flow_artifacts.py", "tools/git_change_adapter.py", "tools/http_binding_v1.py",
    "tools/init_skillsrc.py", "tools/json_cli.py", "tools/orchestrate_test_case_revision.py",
    "tools/pipeline6_tail.py", "tools/publish_test_case_bundle.py", "tools/revision_selection.py",
    "tools/run_tests.py", "tools/schema_validation.py", "tools/skillsrc_manifest.py",
    "tools/stack_catalog.py", "tools/test_case_projections.py", "tools/test_classification.py",
    "tools/trace_check.py", "tools/validate_artifact.py",
)
SCHEMA_FILES = (
    "autotest-reviewer-output.schema.json", "baseline-advancement.schema.json",
    "behavior-context-batch-result.schema.json", "behavior-context-plan.schema.json",
    "behavior-context-receipt.schema.json", "canonical-document-delta.schema.json",
    "canonical-test-document.schema.json", "change-record.schema.json",
    "change-scope-audit.schema.json", "change-scope-candidate.schema.json",
    "change-scope-receipt.schema.json", "changed-behavior-context.schema.json",
    "context-marker-output.schema.json", "delta-application-receipt.schema.json",
    "effective-technical-evidence.schema.json", "feature-baseline-receipt.schema.json",
    "orchestrator-output.schema.json", "patch-manifest.schema.json", "pipeline.schema.json",
    "project-discovery-output.schema.json", "run-tests-output.schema.json",
    "scan-project-output.schema.json", "semantic-batch-audit.schema.json",
    "semantic-batch-candidate.schema.json", "semantic-batch-promotion.schema.json",
    "skillsrc-init-output.schema.json", "skillsrc.schema.json", "source-inventory-output.schema.json",
    "tc-generator-output.schema.json", "tc-reviewer-output.schema.json",
    "tc-to-autotest-output.schema.json", "terminal-run-receipt.schema.json",
    "test-classifier-output.schema.json", "test-classifier-reviewer-output.schema.json",
    "test-symbol-registry.schema.json", "trace-document.schema.json",
    "unchanged-document-selection.schema.json",
)
POLICY_FILES = tuple(SKILL_FILES.values()) + ("skills/change-scope/references/change-scope-contract.md", "skills/context-marker/references/context-artifact-contract.md", "skills/test-classifier/references/classification-contract.md", "skills/test-classifier-reviewer/references/review-contract.md", "skills/tc-generator/references/case-generation-contract.md", "skills/tc-reviewer/references/review-verdicts.md", "skills/tc-to-autotest/references/automation-output-contract.md", "skills/autotest-reviewer/references/autotest-review-contract.md", "skills/orchestrate/references/orchestration-contract.md")


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _registry(root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
    files = [{"path": path, "sha256": "sha256:" + hashlib.sha256((root / path).read_bytes()).hexdigest()} for path in sorted(paths)]
    return {"sha256": _canonical_sha256({"files": files}), "files": files}


def materialize_fingerprint_registries(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Build the public closed Pipeline 6 fingerprint registries from current bytes."""
    root = Path(root)
    if contract.get("version") != "6.0":
        raise ValueError("Pipeline 6.0 contract is required for final fingerprints.")
    return {"pipeline_contract": _registry(root, ("contracts/pipeline.json",)), "policy_bundle": _registry(root, POLICY_FILES), "tool_bundle": _registry(root, RUNTIME_FILES), "schema_bundle": _registry(root, tuple(f"schemas/{name}" for name in SCHEMA_FILES))}


def _schema_errors(contract: dict[str, Any], root: Path) -> list[str]:
    try:
        schema = json.loads((root / "schemas" / "pipeline.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["pipeline schema is unavailable"]
    return [f"schema: {item.message}" for item in Draft202012Validator(schema).iter_errors(contract)]


def _semantic_errors(contract: dict[str, Any], root: Path, check_drift: bool) -> list[str]:
    errors: list[str] = []
    if contract.get("version") != "6.0": errors.append("pipeline version must be 6.0")
    if contract.get("core_skills") != CORE_SKILLS: errors.append("core skill registry must exactly match Pipeline 6.0")
    if contract.get("skill_files") != SKILL_FILES: errors.append("skill file registry must exactly match Pipeline 6.0")
    if contract.get("verdicts") != VERDICTS: errors.append("verdict registry must exactly match Pipeline 6.0")
    if contract.get("traceability") != ["requirement", "case", "step", "expectation", "assertion", "file", "symbol", "current_run_evidence"]: errors.append("traceability must exactly match the V3 atomic chain")
    if contract.get("historical_rejections") != HISTORICAL_REJECTIONS: errors.append("historical rejection registry must exactly match Pipeline 6.0")
    if [row.get("id") for row in contract.get("artifacts", [])] != ARTIFACT_IDS: errors.append("artifact registry must exactly match the 30-carrier Pipeline 6.0 universe")
    expected_stages = [(stage, kind) for stage, kind, *_ in STAGE_LAYOUT]
    if [(row.get("id"), row.get("kind")) for row in contract.get("steps", [])] != expected_stages: errors.append("steps must exactly match the 18-stage Pipeline 6.0 lifecycle order")
    for stage, _kind, accepts, forwards, produces, branches in STAGE_LAYOUT:
        row = next((item for item in contract.get("steps", []) if item.get("id") == stage), {})
        expected = {"accepts": accepts, "forwards": forwards, "produces": produces, "rejects": [item for item in ARTIFACT_IDS if item not in accepts]}
        if {key: row.get(key) for key in expected} != expected: errors.append(f"step {stage} carrier must exactly match Pipeline 6.0")
        if branches is None:
            if "branches" in row: errors.append(f"step {stage} must not declare branches")
        elif row.get("branches") != branches:
            errors.append(f"step {stage} branches must exactly match Pipeline 6.0")
        elif produces != list(dict.fromkeys(item for branch in branches for item in branch["produces"])):
            errors.append(f"step {stage} produces must be its ordered branch union")
    known = set(ARTIFACT_IDS)
    available = {"raw_content"}
    for row in contract.get("steps", []):
        for field in ("accepts", "forwards", "produces", "rejects"):
            if any(value not in known for value in row.get(field, [])): errors.append(f"step {row.get('id')} has unknown artifact")
        if any(value not in available for value in row.get("accepts", [])): errors.append(f"step {row.get('id')} accepts artifact before connection")
        available.update(row.get("forwards", [])); available.update(row.get("produces", []))
    if contract.get("transitions") != TRANSITIONS: errors.append("transitions must exactly match Pipeline 6.0 branch routing")
    for relative in (*RUNTIME_FILES, *SCHEMA_FILES, *POLICY_FILES):
        if not (root / relative if relative.startswith("tools/") or relative.startswith("skills/") else root / "schemas" / relative).is_file(): errors.append(f"missing required Pipeline 6 file: {relative}")
    try:
        materialize_fingerprint_registries(root, contract)
    except (OSError, ValueError):
        errors.append("Pipeline 6 fingerprint registries are unavailable")
    if check_drift:
        try:
            if __package__:
                from .render_contract_docs import _rendered_files
            else:
                from render_contract_docs import _rendered_files
            for relative, rendered in _rendered_files(contract).items():
                target = root / relative
                if not target.is_file() or target.read_text(encoding="utf-8") != rendered: errors.append(f"projection drift: {relative}")
        except (ImportError, KeyError, OSError, TypeError): errors.append("projection drift check unavailable")
    return errors


def validate_pipeline_contract(contract: dict[str, Any], root: Path, check_drift: bool = False) -> dict[str, Any]:
    errors = _schema_errors(contract, Path(root)); errors.extend(_semantic_errors(contract, Path(root), check_drift))
    return {"status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    parser = JsonArgumentParser(description=__doc__); parser.add_argument("--root", default="."); parser.add_argument("--full", action="store_true")
    args = parser.parse_args(); root = Path(args.root).resolve()
    try: contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): report = {"status": "failed", "errors": ["contract unavailable"]}
    else: report = validate_pipeline_contract(contract, root, args.full)
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
