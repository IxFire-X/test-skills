#!/usr/bin/env python3
"""Render deterministic human projections of Pipeline 4.0 machine truth."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from tools.json_cli import JsonArgumentParser


MARKER = "Generated from `contracts/pipeline.json`. Do not edit manually."
RUNTIME_SIGNATURE_ORDER = ("create_run", "append_event", "create_attempt", "derive_state", "terminal_result", "exit_code")
RESULT_AXIS_ORDER = ("attempt_state", "completion", "verification", "coverage", "review_independence", "reason_code", "accepted")
POLICY_ORDER = ("cases-only-v1", "local-pilot-v1")
ADAPTER_ORDER = ("pytest:selected-symbols-v1", "maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1", "gradle-wrapper:selected-symbols-v1")
STAGE_ORDER = ("orchestrate", "context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


def _table(rows: list[Mapping[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    header = "| " + " | ".join(title for title, _ in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(f"`{row[key]}`" for _, key in columns) + " |" for row in rows]
    return [header, divider, *body]


def _rework_lines(rework: Mapping[str, Any]) -> list[str]:
    return [
        f"- rework: at most `{rework['max_per_run']}` per run as a `{rework['retry_reason']}` child attempt after `{rework['trigger']}`",
        f"- rework generator: input `{', '.join(rework['generator_input'])}`; output `{rework['generator_output']}`; successor `{rework['successor']}`",
        f"- rework review: `{rework['r2_review']}`; never reworked: `{', '.join(rework['not_reworked'])}`; after r2 rejected: `{rework['after_r2_rejected']}`",
    ]


def _self_review_lines(self_review: Mapping[str, Any]) -> list[str]:
    return [
        f"- isolation `none`: `{self_review['isolation_none']}`; axis `{self_review['independence_axis']}`: `{', '.join(self_review['values'])}`",
        f"- `SELF` without `{self_review['accept_flag']}`: accepted `false`, reason `{self_review['self_reason_code']}`",
        f"- review inputs above `{self_review['default_context_bytes']}` bytes (run parameter) without isolation: warning `{self_review['context_warning']}`",
    ]


def _mode_lines(modes: Mapping[str, Any]) -> list[str]:
    compact = modes["compact-v1"]
    return [
        f"- review mode `{modes['setting']}`: `{', '.join(modes['values'])}`; default `{modes['default']}`",
        f"- `compact-v1` model input: `{compact['model_input']}` (`{compact['projection']}`, `{compact['projection_binding']}`); never model inputs: `{', '.join(compact['not_model_inputs'])}`",
        f"- `compact-v1` plan `{compact['plan_schema']}`, answer `{compact['answer_schema']}`; packing `{compact['packing']}`; `{compact['pair_guarantee']}`; areas `{', '.join(compact['areas'])}`",
        f"- `compact-v1` order `{compact['invocation_order']}` (`{compact['batch_flag']}`); answer checks `{', '.join(compact['answer_checks'])}`",
        f"- `compact-v1` corrections `{compact['corrections']}`; carry key `{', '.join(compact['carry_key'])}`",
    ]


def _amendment_lines(contract: Mapping[str, Any]) -> list[str]:
    """Opt-in amendments (empty for a contract without them)."""
    lines: list[str] = []
    for row in contract.get("contract_amendments") or []:
        lines.append(f"- `{row['id']}` (wave {row['wave']}, `{row['status']}`, opt-in `{str(row['opt_in']).lower()}`): §{', §'.join(row['sections'])} — `{row['document']}`")
    for stage in contract.get("optional_lifecycle_stages") or []:
        lines.append(f"- optional stage `{stage['stage']}` between `{stage['after']}` and `{stage['before']}`; requires `{', '.join(stage['requires'])}`; "
                     f"mutates `{stage['mutates']}`; writes `{stage['writes']}`; proves `{stage['proves']}`; receipt `{stage['receipt']}`; axis `{stage['axis']}`; "
                     f"never changes `{', '.join(stage['never_changes'])}`")
    java = (contract.get("mutation_tooling") or {}).get("java")
    if java:
        lines.append(f"- mutation tool (Java): `{java['tool']}:{java['version']}` + `{java['plugin']}:{java['plugin_version']}`, pins `{java['pins']}`, "
                     f"resolution `{java['resolution']}`, launcher `{java['launcher']}`, consent `{', '.join(java['consent'])}`, digest mismatch `{java['digest_mismatch']}`, "
                     f"mutators `{java['mutators']}`, report `{java['report']}`; Python: `{contract['mutation_tooling']['python']}`")
    runner = contract.get("model_runner")
    if runner:
        lines.append(f"- model runner `{runner['setting']}`: `{', '.join(runner['values'])}`; default `{runner['default']}`; presets `{', '.join(runner['presets'])}`; "
                     f"custom template `{runner['custom_template']}`; `.skillsrc` fields `{', '.join(runner['skillsrc_fields'])}`; invocation `{runner['invocation']}`; "
                     f"wait action `{runner['wait_action']}`; tries per part `{runner['tries_per_part']}`; standalone `{runner['standalone_command']}`")
        lines.append(f"- runner evidence `{', '.join(runner['evidence'])}` → axis `{runner['evidence_axis']}`; `{runner['require_flag']}` rejects lower levels with `{runner['require_reason_code']}`")
    for name, axis in (contract.get("optional_result_axes") or {}).items():
        lines.append(f"- optional axis `{name}`: `{', '.join(axis['values'])}`; nullable `{axis['nullable']}`")
    return lines


def _rendered_files(contract: Mapping[str, Any]) -> dict[str, str]:
    contracts = ["# Contract Reference", "", MARKER, "", "## Core Skills", ""]
    contracts.extend(f"- `{name}` — `{contract['skill_files'][name]}`" for name in contract["core_skills"])
    artifact_rows = [{**row, "component_state": row.get("component_state", "-")} for row in contract["artifact_registry"]]
    schema_rows = [{**row, "component_state": row.get("component_state", "-")} for row in contract["schema_registry"]]
    stages = {row["stage"]: {**row, "profiles": ", ".join(row["profiles"])} for row in contract["stage_registry"]}
    contracts.extend(["", "## Model stage registry", "", *_table([stages[name] for name in STAGE_ORDER], [("Stage", "stage"), ("Role", "role"), ("Role policy", "role_policy"), ("Cardinality", "cardinality"), ("Profiles", "profiles")])])
    contracts.extend(["", "## Projection profiles", "", *_table(contract["projection_profiles"], [("Profile", "id"), ("Format", "format"), ("Mode", "mode"), ("Tenant status", "tenant_status")])])
    contracts.extend(["", "## Canonical rework", "", *_rework_lines(contract["reviewer_session_contract"]["rework"])])
    contracts.extend(["", "## Review without isolation", "", *_self_review_lines(contract["reviewer_session_contract"]["self_review"])])
    contracts.extend(["", "## Review modes", "", *_mode_lines(contract["reviewer_session_contract"]["modes"])])
    amendments = _amendment_lines(contract)
    if amendments:
        contracts.extend(["", "## Contract amendments (opt-in)", "", *amendments])
    contracts.extend(["", "## Artifact registry", "", *_table(artifact_rows, [("Artifact", "id"), ("Phase", "phase"), ("Status", "implementation_status"), ("Semantic ready", "semantic_ready"), ("Component state", "component_state")]), "", "## Schema registry", "", *_table(schema_rows, [("Schema", "id"), ("Phase", "phase"), ("Status", "implementation_status"), ("Target version", "target_version"), ("Semantic ready", "semantic_ready"), ("Component state", "component_state")]), ""])
    pipeline = [f"# Pipeline: {contract['pipeline']}", "", MARKER, "", f"Version: `{contract['version']}`", "", "## Phase 1 public runtime seam", ""]
    pipeline.extend(f"- `{name}{contract['runtime_signatures'][name]}`" for name in RUNTIME_SIGNATURE_ORDER)
    pipeline.extend(["", "## Event order", "", " → ".join(f"`{event['event_type']}`" for event in contract["event_order"]), "", "## Global ordering constraints", ""])
    pipeline.extend(f"- `{constraint['before']} → {constraint['after']}`" + (" (narrow exception)" if constraint.get("narrow_exception") else "") for constraint in contract["global_event_constraints"])
    reviewer = contract["reviewer_session_contract"]
    pipeline.extend(["", "## Reviewer session", "", f"- logical reviews `{reviewer['logical_review_count']}`; fresh contexts `{reviewer['session_count']}`; order `{reviewer['invocation_order']}`; successful verdicts `{reviewer['successful_verdicts']}`", f"- coverage `{', '.join(reviewer['coverage'])}`; aggregation `{reviewer['aggregation']}`; incomplete `{reviewer['incomplete']}`", f"- terminal pre-verdict abort verdicts `{reviewer['pre_verdict_abort']['verdicts']}`", f"- forbidden: `{', '.join(reviewer['forbidden'])}`", *_rework_lines(reviewer["rework"]), *_self_review_lines(reviewer["self_review"]), *_mode_lines(reviewer["modes"]), "", "## Physical lifecycle", "", " → ".join(f"`{step}`" for step in contract["physical_lifecycle"]), "", "## Result axes", ""])
    for name in RESULT_AXIS_ORDER:
        axis = contract["result_axes"][name]
        if name in {"accepted", "reason_code"}:
            pipeline.append(f"- `{name}`: preterminal `{axis['preterminal']}`; terminal `{axis['terminal']}`")
        else:
            pipeline.append(f"- `{name}`: `{', '.join(axis['values'])}`; nullable `{axis['nullable']}`")
    amendments = _amendment_lines(contract)
    if amendments:
        pipeline.extend(["", "## Opt-in amendments", "", *amendments])
    pipeline.extend(["", "## Normative result tuples", ""])
    pipeline.extend(f"- `{name}`: `{json.dumps(contract['result_tuples'][name], ensure_ascii=False, sort_keys=True)}`" for name in ("complete_fail", "execution_unknown", "pre_execution_rework", "early_fatal", "review_context_limit", "invalid_finalization"))
    policies = {row["id"]: row for row in contract["policy_profiles"]}
    adapters = {row["id"]: row for row in contract["adapter_registry"]}
    pipeline.extend(["", "## Policies", "", *_table([policies[name] for name in POLICY_ORDER], [("Policy", "id"), ("Version", "version"), ("Semantic ready", "semantic_ready")]), "", "## Adapters", "", *_table([adapters[name] for name in ADAPTER_ORDER], [("Adapter", "id"), ("Phase", "phase"), ("Status", "implementation_status"), ("Version", "version")]), "", "## Acceptance predicates", ""])
    for profile in POLICY_ORDER:
        predicates = contract["acceptance_predicates"][profile]
        pipeline.append(f"- `{profile}`: `{', '.join(predicates)}`")
    pipeline.extend(["", "## Exit priority", ""])
    pipeline.extend(f"- `{rule['when']} => {rule['code']}`" for rule in contract["exit_priority"])
    release = contract["release_qualification"]
    pipeline.extend([
        "", "## Release qualification", "",
        f"- package: `{release['package_version']}` via `{release['manifest_path']}`",
        f"- compatibility contract: `{release['compatibility_contract_version']}`",
        f"- execution profile: `{release['execution_profile_version']}`",
        f"- release eval: `{release['release_eval_policy']}` / `{release['scenario_suite']}`",
        f"- component state: `{release['component_state']}`; ready tuple: `{json.dumps(release['ready_tuple'])}`",
        f"- core-pilot N/A: `{', '.join(release['not_applicable'])}`",
    ])
    pipeline.append("")
    return {contract["projections"]["contracts"]: "\n".join(contracts), contract["projections"]["pipeline"]: "\n".join(pipeline)}


def main() -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    drift: list[str] = []
    for relative_path, contents in _rendered_files(contract).items():
        path = root / relative_path
        if arguments.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != contents:
                drift.append(relative_path)
        else:
            path.write_text(contents, encoding="utf-8", newline="\n")
    if drift:
        print("projection drift: " + ", ".join(drift))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
