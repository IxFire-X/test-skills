#!/usr/bin/env python3
"""Validate the authoritative portable pipeline contract."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

try:
    from json_cli import JsonArgumentParser
except ModuleNotFoundError:  # imported as tools.contract_check by tests
    from tools.json_cli import JsonArgumentParser

from jsonschema import Draft202012Validator

CORE_SKILLS = ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
STAGES = [
    ("context-marker", "skill"),
    ("tc-generator", "skill"),
    ("tc-reviewer", "skill"),
    ("tc-to-autotest", "skill"),
    ("autotest-reviewer", "skill"),
    ("run-tests", "tool"),
    ("trace-check", "tool"),
]
ARTIFACT_IDS = {
    "raw_content",
    "analytics_documentation",
    "source_code_and_diff",
    "generated_test_cases",
    "validation_report",
    "corrected_test_cases",
    "automation_matrix",
    "generated_test_files",
    "generated_test_methods",
    "autotest_review",
    "run_tests_verdict",
    "execution_evidence",
    "trace_audit",
}
CAPABILITIES = {
    "java": {"language": "java", "framework": "junit5", "generation": True, "review": True, "execution": True, "status": "supported"},
    "python": {"language": "python", "framework": "pytest", "generation": True, "review": True, "execution": True, "status": "supported"},
    "typescript": {"language": "typescript", "framework": "jest", "generation": False, "review": False, "execution": False, "status": "experimental"},
    "go": {"language": "go", "framework": "go-testing", "generation": False, "review": False, "execution": False, "status": "experimental"},
}
PROJECTION_PATHS = {"contracts": "CONTRACTS.md", "pipeline": "PIPELINE.md"}
REQUIRED_TRANSITIONS = [
    {"from": "tc-reviewer", "when": {"review_verdict": "ПРИНЯТО"}, "transform": "continue_with_original"},
    {"from": "tc-reviewer", "when": {"review_verdict": "AUTO_FIX_APPLIED"}, "transform": "continue_with_corrected"},
    {"from": "tc-reviewer", "when": {"review_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, "transform": "stop_rework"},
    {"from": "autotest-reviewer", "when": {"review_verdict": "ПРИНЯТО"}, "transform": "continue_with_original"},
    {"from": "autotest-reviewer", "when": {"review_verdict": "AUTO_FIX_APPLIED"}, "transform": "continue_with_corrected"},
    {"from": "autotest-reviewer", "when": {"review_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, "transform": "stop_rework"},
    {"from": "run-tests", "when": {"execution_verdict": "PASS"}, "transform": "continue_trace_audit"},
    {"from": "run-tests", "when": {"execution_verdict": "FAIL"}, "transform": "stop_failed"},
    {"from": "run-tests", "when": {"execution_verdict": "NOT_RUNNABLE"}, "transform": "stop_not_runnable"},
    {"from": "trace-check", "when": {"execution_verdict": "PASS", "trace_verdict": "PASS"}, "transform": "complete"},
    {"from": "trace-check", "when": {"execution_verdict": "PASS", "trace_verdict": "FAIL"}, "transform": "stop_trace_failed"},
]


def _load_renderer_module():
    path = Path(__file__).with_name("render_contract_docs.py")
    spec = importlib.util.spec_from_file_location("render_contract_docs", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load renderer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_errors(contract: dict[str, Any], root: Path) -> list[str]:
    schema_path = root / "schemas" / "pipeline.schema.json"
    if not schema_path.is_file():
        return [f"schema not found: {schema_path}"]
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"schema unreadable: {error}"]
    validator = Draft202012Validator(schema)
    return [f"schema: {error.message}" for error in sorted(validator.iter_errors(contract), key=str)]


def _is_safe_projection_path(path: object, expected: str) -> bool:
    if path != expected or not isinstance(path, str):
        return False
    windows_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path)
    return not (
        windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or posix_path.is_absolute()
        or ".." in windows_path.parts
        or ".." in posix_path.parts
        or "/" in path
        or "\\" in path
    )


def _semantic_errors(contract: dict[str, Any], root: Path, check_drift: bool) -> list[str]:
    errors: list[str] = []
    if contract.get("core_skills") != CORE_SKILLS:
        errors.append("core skill registry must exactly match the portable core")

    artifact_ids = [artifact.get("id") for artifact in contract.get("artifacts", [])]
    known_artifacts = {artifact_id for artifact_id in artifact_ids if isinstance(artifact_id, str)}
    if len(artifact_ids) != len(known_artifacts):
        errors.append("artifact ids must be unique")
    if known_artifacts != ARTIFACT_IDS:
        errors.append("artifact registry must exactly match the portable trace chain")

    steps = contract.get("steps", [])
    stage_ids = [step.get("id") for step in steps]
    if len(stage_ids) != len(set(stage_ids)):
        errors.append("step ids must be unique")
    if [(step.get("id"), step.get("kind")) for step in steps] != STAGES:
        errors.append("stages must use the exact portable order and skill/tool identities")

    available = {"raw_content"}
    for step in steps:
        step_id = step.get("id", "<unknown>")
        for field in ("accepts", "forwards", "produces", "rejects"):
            for artifact_id in step.get(field, []):
                if artifact_id not in known_artifacts:
                    errors.append(f"step {step_id} {field} unknown artifact: {artifact_id}")
        for artifact_id in step.get("accepts", []):
            if artifact_id not in available:
                errors.append(f"step {step_id} accepts artifact before it is connected: {artifact_id}")
        for artifact_id in step.get("forwards", []):
            if artifact_id not in step.get("accepts", []) and artifact_id not in step.get("produces", []):
                errors.append(f"step {step_id} forwards artifact without provenance: {artifact_id}")
        available.update(step.get("forwards", []))
        available.update(step.get("produces", []))

    step_map = {step.get("id"): step for step in steps}
    required_links = {
        "tc-to-autotest": ("produces", {"generated_test_files", "generated_test_methods"}),
        "autotest-reviewer": ("accepts", {"generated_test_files", "generated_test_methods"}),
        "run-tests": ("accepts", {"generated_test_files", "generated_test_methods"}),
        "trace-check": ("accepts", {"generated_test_methods", "execution_evidence"}),
    }
    for step_id, (field, required_artifacts) in required_links.items():
        actual = set(step_map.get(step_id, {}).get(field, []))
        if not required_artifacts <= actual:
            errors.append(f"step {step_id} must {field} generated file/method trace artifacts")

    terminal_transforms = {
        "FAIL": "stop_failed",
        "NOT_RUNNABLE": "stop_not_runnable",
    }
    for verdict, expected_transform in terminal_transforms.items():
        transition = next(
            (
                item
                for item in contract.get("transitions", [])
                if item.get("from") == "run-tests"
                and item.get("when", {}).get("execution_verdict") == verdict
            ),
            None,
        )
        if transition is not None and transition.get("transform") != expected_transform:
            errors.append(f"terminal transform for {verdict} must be {expected_transform}")
    if contract.get("transitions") != REQUIRED_TRANSITIONS:
        errors.append("review and execution transitions must exactly preserve the portable branch policy")
    if contract.get("capabilities") != list(CAPABILITIES.values()):
        errors.append("capability baseline must preserve Java/Python support and TypeScript/Go non-execution")
    if contract.get("artifact_policy", {}).get("persistent_root") != "docs/to_do":
        errors.append("artifact_policy persistent_root must be exactly docs/to_do")

    projections = contract.get("projections", {})
    for name, expected_path in PROJECTION_PATHS.items():
        path = projections.get(name)
        if not _is_safe_projection_path(path, expected_path):
            errors.append(f"projection path for {name} must be the exact safe target {expected_path}")
            continue
        if check_drift:
            target = root / path
            if not target.is_file():
                errors.append(f"projection path missing: {path}")
                continue
            renderer = _load_renderer_module()
            expected = getattr(renderer, f"render_{name}")(contract)
            if target.read_text(encoding="utf-8") != expected:
                errors.append(f"projection drift: {path}")
    return errors


def validate_pipeline_contract(contract: dict[str, Any], root: Path, check_drift: bool = False) -> dict[str, Any]:
    """Validate JSON Schema, semantic branches, and optional document drift."""
    root = Path(root)
    errors = _schema_errors(contract, root)
    errors.extend(_semantic_errors(contract, root, check_drift))
    return {"status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="Validate contracts/pipeline.json")
    parser.add_argument("--root", default=".", help="Portable pack root")
    parser.add_argument("--full", action="store_true", help="Also verify generated projections")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    contract_path = root / "contracts" / "pipeline.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        report = {"status": "failed", "errors": [f"contract unreadable: {error}"]}
    else:
        report = validate_pipeline_contract(contract, root, check_drift=args.full)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
