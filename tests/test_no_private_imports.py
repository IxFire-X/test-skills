"""V1 (A3-4): no module of the package reaches into the private names of another module of the package.

``from tools.x import _y`` and ``x._y`` (and ``tools.x._y``) for another module ``x`` of ``tools/`` — lazy imports inside functions
included — are what makes a rename of a private name break its users without a warning.  The execution files (``exec_*``) and the
facade of the core for them (``execution_core``) have none: they take the former private names from ``tools.execution_core``.
The rest are listed below with their counts and the list may only shrink: the test wants the found uses to be exactly the listed
ones, so a new use is refused and a removed one has to be taken off the list in the same change.  A public alias
(``public_name = _private_name`` in the module that owns it) is not a use; importing the private name through it would be.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
EXECUTION_FILES = ("execution_core",)


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def private_uses(tools: Path = TOOLS) -> collections.Counter[tuple[str, str, str]]:
    modules = {path.stem for path in tools.glob("*.py")}
    found: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for path in sorted(tools.glob("*.py")):
        me = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}  # a local name -> the module of the package it stands for
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                parts = node.module.split(".")
                if parts[0] == "tools" and len(parts) == 2 and parts[1] in modules and parts[1] != me:
                    found.update((me, parts[1], alias.name) for alias in node.names if _is_private(alias.name))
                elif node.module == "tools":
                    aliases.update({alias.asname or alias.name: alias.name for alias in node.names if alias.name in modules and alias.name != me})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "tools" and len(parts) == 2 and parts[1] in modules and parts[1] != me and alias.asname:
                        aliases[alias.asname] = parts[1]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and _is_private(node.attr)):
                continue
            value = node.value
            if isinstance(value, ast.Name) and value.id in aliases:
                found[(me, aliases[value.id], node.attr)] += 1
            elif (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "tools"
                  and value.attr in modules and value.attr != me):
                found[(me, value.attr, node.attr)] += 1
    return found


def _is_exec_file(name: str) -> bool:
    return name.startswith("exec_") or name in EXECUTION_FILES


# The uses outside the execution files that are left; every package that takes one away takes it off this list.
EXCEPTIONS: dict[tuple[str, str, str], int] = {
    ("api", "orchestrate_test_case_revision", "_PROFILE"): 1,
    ("automation_validation", "pilot_state", "_read_attempt_receipt_with_state"): 1,
    ("automation_validation", "pilot_state", "_read_effective_canonical_with_state"): 1,
    ("automation_validation", "pilot_state", "_run_root"): 1,
    ("batch_assembly", "build_context", "_project_id_pattern"): 1,
    ("cases_import", "project_inventory", "_secret_value"): 1,
    ("contract_check", "render_contract_docs", "_rendered_files"): 1,
    ("existing_tests", "pilot_state", "_context_selection_target"): 1,
    ("generated_delta", "pilot_state", "_sealed"): 1,
    ("orchestrate_test_case_revision", "pilot_state", "_CLOSURE_CACHE"): 1,
    ("orchestrate_test_case_revision", "pilot_state", "_generator_input"): 1,
    ("orchestrate_test_case_revision", "pilot_state", "_imported_cases"): 1,
    ("orchestrate_test_case_revision", "pilot_state", "_publish_bound_attempt_receipt"): 1,
    ("orchestrate_test_case_revision", "pilot_state", "_run_root"): 4,
    ("orchestrate_test_case_revision", "run_pipeline", "_scan_context_budget"): 1,
    ("output_guard", "pilot_state", "_run_root"): 1,
    ("pilot_state", "build_context", "_OPENSPEC_PATH"): 1,
    ("pilot_state", "orchestrate_test_case_revision", "_run_imported"): 1,
    ("pilot_state", "project_inventory", "_digest"): 1,
    ("pilot_state", "run_pipeline", "_scan_command"): 1,
    ("pilot_state", "schema_validation", "_json_validation_key"): 1,
    ("pilot_state", "step_size", "_REQUEST_STAGES"): 1,
    ("publish_test_case_bundle", "test_case_projections", "_reject_unknown_profile"): 1,
    ("publish_test_case_bundle", "test_case_projections", "_render_html_preview_validated"): 1,
    ("publish_test_case_bundle", "test_case_projections", "_render_markdown_validated"): 1,
    ("publish_test_case_bundle", "test_case_projections", "_render_zephyr_csv_validated"): 1,
    ("ready", "run_pipeline", "_baseline_blockers"): 1,
    ("ready", "run_pipeline", "_exclusion_note"): 1,
    ("review_canaries", "pilot_state", "_receipt_target"): 1,
    ("review_canaries", "pilot_state", "_run_root"): 5,
    ("review_canaries", "review_view", "_assertion"): 2,
    ("review_legacy", "api", "_out_target"): 1,
    ("review_legacy", "api", "_write"): 1,
    ("review_legacy", "orchestrate_test_case_revision", "_FIXES"): 1,
    ("review_legacy", "orchestrate_test_case_revision", "_PROFILE"): 3,
    ("review_legacy", "orchestrate_test_case_revision", "_PROFILES"): 2,
    ("review_legacy", "orchestrate_test_case_revision", "_canary_report"): 1,
    ("review_legacy", "orchestrate_test_case_revision", "_diag"): 4,
    ("review_legacy", "orchestrate_test_case_revision", "_load_receipt"): 1,
    ("review_legacy", "orchestrate_test_case_revision", "_read_existing_context_receipts"): 2,
    ("review_legacy", "orchestrate_test_case_revision", "_receipt_json"): 2,
    ("run_pipeline", "pilot_state", "_PublicationUnknown"): 1,
    ("run_pipeline", "pilot_state", "_frozen_inventory"): 1,
    ("run_pipeline", "pilot_state", "_generator_input"): 1,
    ("run_pipeline", "pilot_state", "_read_artifact"): 1,
    ("run_pipeline", "pilot_state", "_read_attempt_receipt_with_state"): 1,
    ("run_pipeline", "pilot_state", "_receipt_target"): 2,
    ("run_pipeline", "project_inventory", "_secret_rule"): 1,
    ("run_report", "pilot_state", "_receipt_target"): 1,
    ("run_report", "review_view", "_reference_counts"): 1,
    ("run_report", "run_pipeline", "_durable_status_facts"): 2,
    ("run_report", "step_size", "_requirement_files"): 1,
    ("run_tests", "pilot_state", "_canonical_runner_output"): 1,
    ("scope_pause", "pilot_state", "_generator_input"): 1,
    ("step_size", "pilot_state", "_model_request_target"): 1,
    ("zephyr_csv", "test_case_projections", "_CUSTOM_KEYS"): 1,
    ("zephyr_csv", "test_case_projections", "_HEADERS"): 1,
    ("zephyr_csv", "test_case_projections", "_PRIORITIES"): 1,
    ("zephyr_template_export", "run_pipeline", "_read_run_coordinate"): 1,
    ("zephyr_template_export", "test_case_projections", "_CUSTOM_KEYS"): 1,
    ("zephyr_template_export", "test_case_projections", "_HEADERS"): 1,
    ("zephyr_template_export", "test_case_projections", "_formula_safe"): 1,
}


def test_the_execution_files_and_the_facade_take_no_private_name_of_another_module() -> None:
    offenders = sorted(key for key in private_uses() if _is_exec_file(key[0]))
    assert offenders == []


def test_the_uses_left_are_exactly_the_listed_ones_and_only_ever_fewer() -> None:
    found = private_uses()
    assert not [key for key in EXCEPTIONS if _is_exec_file(key[0])], "the execution files have no exception"
    new = {key: count for key, count in found.items() if not _is_exec_file(key[0]) and count > EXCEPTIONS.get(key, 0)}
    gone = {key: count for key, count in EXCEPTIONS.items() if found.get(key, 0) < count}
    assert new == {}, f"new private uses (take the public name of the owner, or ask for one): {new}"
    assert gone == {}, f"uses that are gone are still listed (take them off EXCEPTIONS): {gone}"


def test_a_public_alias_is_no_use_but_importing_a_private_name_is(tmp_path: Path) -> None:
    (tmp_path / "owner.py").write_text("def _inside():\n    return 1\n\npublic_name = _inside\n", encoding="utf-8")
    (tmp_path / "user.py").write_text("from tools.owner import public_name\n\ndef f():\n    from tools.owner import _inside\n    return _inside\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("from tools import owner\n\nVALUE = owner._inside\n", encoding="utf-8")
    assert dict(private_uses(tmp_path)) == {("user", "owner", "_inside"): 1, ("other", "owner", "_inside"): 1}


def test_no_module_of_the_package_edits_the_import_path() -> None:
    offenders = []
    for path in sorted(TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and node.attr in {"insert", "append", "extend"} and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "path" and isinstance(node.value.value, ast.Name) and node.value.value.id == "sys"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
