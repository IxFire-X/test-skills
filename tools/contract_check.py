#!/usr/bin/env python3
"""Validate the authoritative closed Pipeline 4.0 contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

if __package__:
    from .json_cli import JsonArgumentParser
else:
    from json_cli import JsonArgumentParser


CORE_SKILLS = ["context-marker", "test-classifier", "test-classifier-reviewer", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
SKILL_FILES = {name: f"skills/{name}/SKILL.md" for name in CORE_SKILLS}
ARTIFACT_IDS = ["raw_content", "technical_test_inventory", "authorized_behavior_sources", "managed_behavior_context", "technical_test_classification", "classification_review", "effective_technical_evidence", "candidate_document", "candidate_bundle_receipt", "validation_report", "successor_document", "successor_bundle_receipt", "effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review", "run_result", "trace_document", "trace_audit", "orchestrator_output"]
STAGES = [("source-inventory", "tool"), ("context-marker", "skill"), ("test-classifier", "skill"), ("test-classifier-reviewer", "skill"), ("tc-generator", "skill"), ("publish-candidate", "tool"), ("tc-reviewer", "skill"), ("revision-orchestrator", "tool"), ("tc-to-autotest", "skill"), ("autotest-reviewer", "skill"), ("run-tests", "tool"), ("build-trace", "tool"), ("trace-check", "tool"), ("finalize-orchestration", "tool")]
STAGE_CARRIERS = {
    "source-inventory": {"accepts":["raw_content"],"forwards":["raw_content"],"produces":["technical_test_inventory","authorized_behavior_sources"],"rejects":[]},
    "context-marker": {"accepts":["raw_content","technical_test_inventory","authorized_behavior_sources"],"forwards":["technical_test_inventory","authorized_behavior_sources"],"produces":["managed_behavior_context"],"rejects":[]},
    "test-classifier": {"accepts":["technical_test_inventory","authorized_behavior_sources","managed_behavior_context"],"forwards":["technical_test_inventory","authorized_behavior_sources","managed_behavior_context"],"produces":["technical_test_classification"],"rejects":[]},
    "test-classifier-reviewer": {"accepts":["technical_test_inventory","authorized_behavior_sources","managed_behavior_context","technical_test_classification"],"forwards":["managed_behavior_context"],"produces":["classification_review","effective_technical_evidence"],"rejects":[]},
    "tc-generator": {"accepts":["managed_behavior_context"],"forwards":["managed_behavior_context"],"produces":["candidate_document"],"rejects":[]},
    "publish-candidate": {"accepts":["candidate_document"],"forwards":["candidate_document"],"produces":["candidate_bundle_receipt"],"rejects":[]},
    "tc-reviewer": {"accepts":["candidate_document"],"forwards":["candidate_document"],"produces":["validation_report","successor_document"],"rejects":[]},
    "revision-orchestrator": {"accepts":["candidate_document","candidate_bundle_receipt","validation_report","successor_document"],"forwards":["candidate_bundle_receipt","validation_report"],"produces":["successor_bundle_receipt","effective_document","effective_bundle_receipt"],"rejects":[]},
    "tc-to-autotest": {"accepts":["effective_document","effective_bundle_receipt"],"forwards":["effective_document","effective_bundle_receipt"],"produces":["automation_artifact"],"rejects":[]},
    "autotest-reviewer": {"accepts":["effective_document","automation_artifact"],"forwards":["effective_document","automation_artifact"],"produces":["autotest_review"],"rejects":[]},
    "run-tests": {"accepts":["effective_document","automation_artifact","autotest_review"],"forwards":["effective_document","automation_artifact","autotest_review"],"produces":["run_result"],"rejects":[]},
    "build-trace": {"accepts":["effective_document","automation_artifact","run_result"],"forwards":["effective_document","automation_artifact","run_result"],"produces":["trace_document"],"rejects":[]},
    "trace-check": {"accepts":["trace_document"],"forwards":["trace_document"],"produces":["trace_audit"],"rejects":[]},
    "finalize-orchestration": {"accepts":["effective_document","effective_bundle_receipt","automation_artifact","autotest_review","run_result","trace_document","trace_audit"],"forwards":[],"produces":["orchestrator_output"],"rejects":[]},
}
REQUIRED_TRANSITIONS = [
    {"from":"test-classifier-reviewer","when":{"classification_verdict":"ПРИНЯТО"},"transform":"select_effective_technical_evidence"}, {"from":"test-classifier-reviewer","when":{"classification_verdict":"ТРЕБУЕТ ДОРАБОТКИ"},"transform":"stop_classification_rework"},
    {"from":"tc-reviewer","when":{"review_verdict":"ПРИНЯТО"},"transform":"revision_orchestrator_selects_candidate"}, {"from":"tc-reviewer","when":{"review_verdict":"AUTO_FIX_APPLIED"},"transform":"revision_orchestrator_validates_publishes_selects_successor"}, {"from":"tc-reviewer","when":{"review_verdict":"ТРЕБУЕТ ДОРАБОТКИ"},"transform":"stop_rework"},
    {"from":"autotest-reviewer","when":{"review_verdict":"ПРИНЯТО","automation_status":"BLOCKED"},"transform":"build_trace_without_run"}, {"from":"autotest-reviewer","when":{"review_verdict":"ПРИНЯТО","required_symbol_pairs":0},"transform":"build_trace_without_run"}, {"from":"autotest-reviewer","when":{"review_verdict":"ПРИНЯТО","required_symbol_pairs":"one_or_more"},"transform":"run_tests"}, {"from":"autotest-reviewer","when":{"review_verdict":"AUTO_FIX_APPLIED"},"transform":"regenerate_automation_and_review_again"}, {"from":"autotest-reviewer","when":{"review_verdict":"ТРЕБУЕТ ДОРАБОТКИ"},"transform":"stop_rework"},
    {"from":"run-tests","when":{"execution_verdict":"PASS"},"transform":"build_trace_then_trace_check"}, {"from":"run-tests","when":{"execution_verdict":"FAIL"},"transform":"build_trace_then_trace_check"}, {"from":"run-tests","when":{"execution_verdict":"NOT_RUNNABLE"},"transform":"build_trace_then_trace_check"}, {"from":"trace-check","when":{"trace_verdict":"PASS"},"transform":"finalize_orchestration"}, {"from":"trace-check","when":{"trace_verdict":"FAIL"},"transform":"stop_invalid_trace"},
]
VERDICTS = {"classification":["ПРИНЯТО","ТРЕБУЕТ ДОРАБОТКИ"],"review":["ПРИНЯТО","AUTO_FIX_APPLIED","ТРЕБУЕТ ДОРАБОТКИ"],"execution":["PASS","FAIL","NOT_RUNNABLE"],"trace":["PASS","FAIL"]}
TRACEABILITY = ["requirement", "case", "step", "expectation", "assertion", "file", "symbol", "current_run_evidence"]
FORBIDDEN = ("generated_test_cases", "corrected_test_cases", "automation_matrix", "generated_test_methods", "method_id", "run_tests_verdict", "execution_evidence")
SCHEMAS = ("test-symbol-registry.schema.json", "source-inventory-output.schema.json", "test-classifier-output.schema.json", "test-classifier-reviewer-output.schema.json", "effective-technical-evidence.schema.json", "canonical-test-document.schema.json", "tc-reviewer-output.schema.json", "tc-to-autotest-output.schema.json", "autotest-reviewer-output.schema.json", "run-tests-output.schema.json", "trace-document.schema.json", "orchestrator-output.schema.json", "pipeline.schema.json")
RUNTIME_TOOLS = ("tools/test_classification.py",)


def _schema_errors(contract: dict[str, Any], root: Path) -> list[str]:
    try:
        schema = json.loads((root / "schemas" / "pipeline.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["pipeline schema is unavailable"]
    return [f"schema: {item.message}" for item in Draft202012Validator(schema).iter_errors(contract)]


def _semantic_errors(contract: dict[str, Any], root: Path, check_drift: bool) -> list[str]:
    errors: list[str] = []
    if contract.get("version") != "4.0": errors.append("pipeline version must be 4.0")
    if contract.get("core_skills") != CORE_SKILLS: errors.append("core skill registry must be preserved")
    if contract.get("skill_files") != SKILL_FILES: errors.append("skill file registry must be preserved")
    if contract.get("verdicts") != VERDICTS: errors.append("verdict registry must exactly match Pipeline 4.0")
    if [row.get("id") for row in contract.get("artifacts", [])] != ARTIFACT_IDS: errors.append("artifact registry must exactly match Pipeline 4.0 lifecycle")
    if [(row.get("id"), row.get("kind")) for row in contract.get("steps", [])] != STAGES: errors.append("steps must exactly match Pipeline 4.0 lifecycle order")
    for step in contract.get("steps", []):
        expected = STAGE_CARRIERS.get(step.get("id"))
        if expected is not None and {field: step.get(field) for field in ("accepts", "forwards", "produces", "rejects")} != expected:
            errors.append(f"step {step.get('id')} carrier must exactly match Pipeline 4.0 lifecycle")
    available = {"raw_content"}
    known = set(ARTIFACT_IDS)
    for step in contract.get("steps", []):
        for field in ("accepts", "forwards", "produces", "rejects"):
            if any(value not in known for value in step.get(field, [])): errors.append(f"step {step.get('id')} has unknown artifact")
        if any(value not in available for value in step.get("accepts", [])): errors.append(f"step {step.get('id')} accepts artifact before connection")
        available.update(step.get("forwards", [])); available.update(step.get("produces", []))
    text = json.dumps(contract, ensure_ascii=False, sort_keys=True)
    if any(value in text for value in FORBIDDEN): errors.append("V2 artifact vocabulary is forbidden")
    if contract.get("transitions") != REQUIRED_TRANSITIONS: errors.append("transitions must exactly match Pipeline 4.0 branch routing")
    if contract.get("traceability") != TRACEABILITY: errors.append("traceability must exactly match the V3 atomic chain")
    for name in SCHEMAS:
        path = root / "schemas" / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema": errors.append(f"invalid schema: {name}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError): errors.append(f"missing schema: {name}")
    for relative in RUNTIME_TOOLS:
        if not (root / relative).is_file():
            errors.append(f"missing runtime tool: {relative}")
    if check_drift:
        try:
            if __package__:
                from .render_contract_docs import _rendered_files
            else:
                from render_contract_docs import _rendered_files
            for relative, rendered in _rendered_files(contract).items():
                target = root / relative
                if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
                    errors.append(f"projection drift: {relative}")
        except (ImportError, KeyError, OSError, TypeError):
            errors.append("projection drift check unavailable")
    return errors


def validate_pipeline_contract(contract: dict[str, Any], root: Path, check_drift: bool = False) -> dict[str, Any]:
    errors = _schema_errors(contract, Path(root)); errors.extend(_semantic_errors(contract, Path(root), check_drift))
    return {"status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    parser = JsonArgumentParser(description=__doc__); parser.add_argument("--root", default="."); parser.add_argument("--full", action="store_true")
    args = parser.parse_args(); root = Path(args.root).resolve()
    try: contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): report = {"status":"failed", "errors":["contract unavailable"]}
    else: report = validate_pipeline_contract(contract, root, args.full)
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
