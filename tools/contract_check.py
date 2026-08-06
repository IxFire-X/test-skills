#!/usr/bin/env python3
"""Validate the authoritative portable pipeline contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


TERMINAL_TRANSFORMS = {
    "PASS": "complete",
    "FAIL": "stop_failed",
    "NOT_RUNNABLE": "stop_not_runnable",
}
PROJECTION_RENDERERS = {
    "contracts": "render_contracts",
    "pipeline": "render_pipeline",
}


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


def _is_safe_relative_path(path: str) -> bool:
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts and path != ""


def _semantic_errors(contract: dict[str, Any], root: Path, check_drift: bool) -> list[str]:
    errors: list[str] = []
    artifact_ids = [artifact.get("id") for artifact in contract.get("artifacts", [])]
    known_artifacts = {artifact_id for artifact_id in artifact_ids if isinstance(artifact_id, str)}
    if len(artifact_ids) != len(known_artifacts):
        errors.append("artifact ids must be unique")
    for step in contract.get("steps", []):
        step_id = step.get("id", "<unknown>")
        for field in ("accepts", "forwards", "produces", "rejects"):
            for artifact_id in step.get(field, []):
                if artifact_id not in known_artifacts:
                    errors.append(f"step {step_id} {field} unknown artifact: {artifact_id}")
    transitions = contract.get("transitions", [])
    observed: dict[str, str] = {}
    for transition in transitions:
        when = transition.get("when", {})
        verdict = when.get("execution_verdict")
        if verdict in TERMINAL_TRANSFORMS:
            transform = transition.get("transform")
            if transform != TERMINAL_TRANSFORMS[verdict]:
                errors.append(
                    f"terminal transform for {verdict} must be "
                    f"{TERMINAL_TRANSFORMS[verdict]!r}, got {transform!r}"
                )
            if verdict in observed:
                errors.append(f"duplicate terminal transition for {verdict}")
            observed[verdict] = str(transform)
    for verdict in TERMINAL_TRANSFORMS:
        if verdict not in observed:
            errors.append(f"missing terminal transition for {verdict}")

    projections = contract.get("projections", {})
    for name, renderer_name in PROJECTION_RENDERERS.items():
        path = projections.get(name)
        if not isinstance(path, str) or not _is_safe_relative_path(path):
            errors.append(f"projection path for {name} must be a safe relative path")
            continue
        target = root / path
        if check_drift:
            if not target.is_file():
                errors.append(f"projection path missing: {path}")
                continue
            renderer = _load_renderer_module()
            expected = getattr(renderer, renderer_name)(contract)
            actual = target.read_text(encoding="utf-8")
            if actual != expected:
                errors.append(f"projection drift: {path}")

    for capability in contract.get("capabilities", []):
        if capability.get("language") in {"typescript", "go"} and capability.get("execution"):
            errors.append(f"unsupported execution capability: {capability['language']}")
    return errors


def validate_pipeline_contract(contract: dict[str, Any], root: Path, check_drift: bool = False) -> dict[str, Any]:
    """Validate JSON Schema, semantic branches, and optional document drift."""
    root = Path(root)
    errors = _schema_errors(contract, root)
    errors.extend(_semantic_errors(contract, root, check_drift))
    return {"status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate contracts/pipeline.json")
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
