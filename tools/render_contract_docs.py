#!/usr/bin/env python3
"""Render deterministic Markdown projections from contracts/pipeline.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from .json_cli import JsonArgumentParser, emit_error
else:  # direct CLI execution
    from json_cli import JsonArgumentParser, emit_error


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


def _cell(value: Any) -> str:
    """Render a contract value as a stable Markdown-safe code cell."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "`" + text.replace("`", "\\`").replace("|", "\\|") + "`"


def _list_cell(values: list[str]) -> str:
    return ", ".join(_cell(value) for value in values) or "—"


def render_pipeline(contract: dict[str, Any]) -> str:
    lines = [
        f"# Pipeline: {contract['pipeline']}",
        "",
        MARKER,
        "",
        f"Version: {_cell(contract['version'])}",
        "",
        "## Steps",
        "",
        "| Step | `kind` | `accepts` | `forwards` | `produces` | `rejects` | `branches` |",
        "|---|---|---|---|---|---|---|",
    ]
    for step in contract["steps"]:
        lines.append(
            "| {id} | {kind} | {accepts} | {forwards} | {produces} | {rejects} | {branches} |".format(
                id=_cell(step["id"]),
                kind=_cell(step["kind"]),
                accepts=_list_cell(step["accepts"]),
                forwards=_list_cell(step["forwards"]),
                produces=_list_cell(step["produces"]),
                rejects=_list_cell(step["rejects"]),
                branches=_cell(step.get("branches", [])),
            )
        )
    lines.extend(["", "## Transitions", "", "| From | Predicates | Transform |", "|---|---|---|"])
    for transition in contract["transitions"]:
        predicates = ", ".join(f"{_cell(key)} = {_cell(value)}" for key, value in transition["when"].items())
        lines.append(f"| {_cell(transition['from'])} | {predicates or '—'} | {_cell(transition['transform'])} |")
    lines.append("")
    return "\n".join(lines)


def render_contracts(contract: dict[str, Any]) -> str:
    lines = ["# Contract Reference", "", MARKER, "", "## Artifacts", "", "| Artifact | Description |", "|---|---|"]
    for artifact in contract["artifacts"]:
        lines.append(f"| {_cell(artifact['id'])} | {artifact['description']} |")
    lines.extend(["", "## Canonical skill files", "", "| Skill | Path |", "|---|---|"])
    for skill in contract["core_skills"]:
        lines.append(f"| {_cell(skill)} | {_cell(contract['skill_files'][skill])} |")
    lines.extend(["", "## Historical rejection registry", "", "| Component | Version | Live status |", "|---|---|---|"])
    for row in contract["historical_rejections"]:
        lines.append(f"| {_cell(row['component'])} | {_cell(row['version'])} | {_cell(row['live_status'])} |")
    lines.extend(["", "## Verdict enums", "", "| Verdict type | Values |", "|---|---|"])
    for verdict_type, values in contract["verdicts"].items():
        lines.append(f"| {_cell(verdict_type)} | {_list_cell(values)} |")
    lines.extend(["", "## Transitions", "", "| From | Predicates | Transform |", "|---|---|---|"])
    for transition in contract["transitions"]:
        predicates = ", ".join(f"{_cell(key)} = {_cell(value)}" for key, value in transition["when"].items())
        lines.append(f"| {_cell(transition['from'])} | {predicates or '—'} | {_cell(transition['transform'])} |")
    lines.extend(["", "## Language capabilities", "", "| Language | Framework | Generation | Review | Execution | Status |", "|---|---|---|---|---|---|"])
    for capability in contract["capabilities"]:
        lines.append(
            "| {language} | {framework} | {generation} | {review} | {execution} | {status} |".format(
                **{key: str(value).lower() if isinstance(value, bool) else value for key, value in capability.items()}
            )
        )
    policy = contract["artifact_policy"]
    lines.extend([
        "",
        "## Artifact policy",
        "",
        f"- Persistent artifacts: {_cell(policy['persistent_root'])}",
        f"- Generated test source: {policy['generated_test_source']}",
        "",
        "## Traceability",
        "",
        " -> ".join(contract["traceability"]),
        "",
    ])
    return "\n".join(lines)


def _rendered_files(contract: dict[str, Any]) -> dict[str, str]:
    return {
        contract["projections"]["contracts"]: render_contracts(contract),
        contract["projections"]["pipeline"]: render_pipeline(contract),
    }


def main() -> int:
    parser = JsonArgumentParser(description="Render contract Markdown projections")
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
        emit_error(f"input error: {error}")
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
