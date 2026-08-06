#!/usr/bin/env python3
"""Render deterministic Markdown projections from contracts/pipeline.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MARKER = "Generated from `contracts/pipeline.json`. Do not edit manually."
PROJECTION_PATHS = {"contracts": "CONTRACTS.md", "pipeline": "PIPELINE.md"}


def _projection_errors(contract: dict[str, Any]) -> list[str]:
    projections = contract.get("projections")
    if not isinstance(projections, dict):
        return ["projections must be an object"]
    errors = []
    for name, expected_path in PROJECTION_PATHS.items():
        if projections.get(name) != expected_path:
            errors.append(f"projection {name} must be exactly {expected_path}")
    if set(projections) != set(PROJECTION_PATHS):
        errors.append("projection keys must be exactly contracts and pipeline")
    return errors


def render_pipeline(contract: dict[str, Any]) -> str:
    rows = ["| Step | Accepts | Produces |", "|---|---|---|"]
    for step in contract["steps"]:
        rows.append(f"| `{step['id']}` | {', '.join(step.get('accepts', []))} | {', '.join(step.get('produces', []))} |")
    return "# Pipeline\n\nGenerated from `contracts/pipeline.json`. Do not edit manually.\n\n" + "\n".join(rows) + "\n"


def render_contracts(contract: dict[str, Any]) -> str:
    lines = ["# Contract Reference", "", MARKER, "", "## Artifacts", "", "| Artifact | Description |", "|---|---|"]
    for artifact in contract["artifacts"]:
        lines.append(f"| `{artifact['id']}` | {artifact['description']} |")
    lines.extend(["", "## Review verdict branches", "", "| Reviewer | Verdict | Transform |", "|---|---|---|"])
    for transition in contract["transitions"]:
        verdict = transition.get("when", {}).get("review_verdict")
        if verdict:
            lines.append(f"| `{transition['from']}` | `{verdict}` | `{transition['transform']}` |")
    lines.extend(["", "## Execution and trace verdict branches", "", "| Stage | Execution verdict | Trace verdict | Transform |", "|---|---|---|---|"])
    for transition in contract["transitions"]:
        when = transition.get("when", {})
        verdict = when.get("execution_verdict")
        if verdict:
            trace_verdict = when.get("trace_verdict", "")
            lines.append(f"| `{transition['from']}` | `{verdict}` | {f'`{trace_verdict}`' if trace_verdict else ''} | `{transition['transform']}` |")
    lines.extend(["", "## Language capabilities", "", "| Language | Framework | Generation | Review | Execution | Status |", "|---|---|---|---|---|---|"])
    for capability in contract["capabilities"]:
        lines.append(
            "| {language} | {framework} | {generation} | {review} | {execution} | {status} |".format(
                **{key: str(value).lower() if isinstance(value, bool) else value for key, value in capability.items()}
            )
        )
    lines.extend(["", "## Artifact policy", "", f"- Persistent artifacts: `{contract['artifact_policy']['persistent_root']}`", f"- Generated test source: {contract['artifact_policy']['generated_test_source']}", "", "## Traceability", "", " → ".join(f"`{item}`" for item in contract["traceability"]), ""])
    return "\n".join(lines)


def _rendered_files(contract: dict[str, Any]) -> dict[str, str]:
    return {
        contract["projections"]["contracts"]: render_contracts(contract),
        contract["projections"]["pipeline"]: render_pipeline(contract),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render contract Markdown projections")
    parser.add_argument("--root", default=".", help="Portable pack root")
    parser.add_argument("--check", action="store_true", help="Fail when checked-in projections drift")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
        errors = _projection_errors(contract)
        if errors:
            print("render failed: " + "; ".join(errors))
            return 2
        files = _rendered_files(contract)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"render failed: {error}")
        return 2
    drifted = []
    for relative_path, contents in files.items():
        target = root / relative_path
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != contents:
                drifted.append(relative_path)
        else:
            target.write_text(contents, encoding="utf-8", newline="\n")
    if drifted:
        print("projection drift: " + ", ".join(drifted))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
