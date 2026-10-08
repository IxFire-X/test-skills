"""Wave 3 contract amendments (2026-10-07): quarantine policy, owned suite tests, suite-update-v1."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.contract_check import validate_pipeline_contract
from tools.render_contract_docs import _rendered_files

WAVE3 = ("optional_disposition_policies", "optional_policy_profiles", "suite_contract", "requirement_identity")


def _contract(root: Path) -> dict:
    return json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))


def test_wave3_sections_are_opt_in_next_to_the_frozen_registries(pack_root: Path) -> None:
    contract = _contract(pack_root)
    assert validate_pipeline_contract(contract, pack_root, check_drift=True) == {"status": "passed", "errors": []}
    waves = {row["wave"]: row for row in contract["contract_amendments"]}
    assert waves[3]["sections"] == ["17.2", "25"] and waves[3]["document"] == waves[2]["document"]
    # The frozen profiles and dispositions are untouched; the new profile is optional and never accepted.
    assert [row["id"] for row in contract["policy_profiles"]] == ["cases-only-v1", "local-pilot-v1"]
    [profile] = contract["optional_policy_profiles"]
    assert profile["id"] == "suite-update-v1" and profile["accepted"] == "not_applicable" and profile["commits"] is False
    assert profile["steps"][0] == "MIGRATION" and profile["steps"][-1] == "SUMMARY"
    policies = contract["optional_disposition_policies"]
    assert policies["defaults"] == {"local-pilot-v1": "quarantine", "suite-update-v1": "quarantine"}  # the default since the wave-3 gate
    assert "cleanup" in policies["values"]  # the frozen §17 item 2 stays selectable
    assert policies["quarantine"]["never_changes"] == ["verification", "coverage", "accepted", "result_tuple"]
    assert contract["suite_contract"]["default_path"] == "test-cases/"
    assert contract["requirement_identity"]["sreq_format"] == "unchanged"


def test_the_quarantine_default_is_declared_by_its_amendment(pack_root: Path) -> None:
    """Independent review 2.2: A4 is no longer opt-in for local-pilot-v1; the contract says so and the checker holds it."""
    contract = _contract(pack_root)
    wave3 = next(row for row in contract["contract_amendments"] if row["wave"] == 3)
    assert wave3["opt_in"] is False and wave3["default_on"] == [{"amendment": "A4", "profile": "local-pilot-v1", "setting": "--disposition-policy",
                                                                 "value": "quarantine", "opt_out": "cleanup", "since": "2026-10-08"}]
    for change in (lambda c: c["optional_disposition_policies"]["defaults"].__setitem__("local-pilot-v1", "cleanup"),
                   lambda c: next(row for row in c["contract_amendments"] if row["wave"] == 3).__setitem__("opt_in", True)):
        changed = copy.deepcopy(contract)
        change(changed)
        errors = validate_pipeline_contract(changed, pack_root)["errors"]
        assert errors, "the checker accepted a contradicting default"
    for text in _rendered_files(contract).values():
        assert "on by default: A4 in `local-pilot-v1`" in text


def test_the_mutation_pins_file_is_checked_against_the_contract(pack_root: Path, tmp_path: Path) -> None:
    """Independent review 2.2: the exact check also covers tools/mutation_tools.json and the triage answer schema."""
    import shutil

    contract = _contract(pack_root)
    assert any(row["id"] == "mutation-triage-output.schema.json" for row in contract["optional_schema_registry"])
    root = tmp_path / "pack"
    shutil.copytree(pack_root / "schemas", root / "schemas")
    shutil.copytree(pack_root / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    pins = json.loads((root / "tools" / "mutation_tools.json").read_text(encoding="utf-8"))
    pins["java"]["pit_version"] = "1.31.0"
    (root / "tools" / "mutation_tools.json").write_text(json.dumps(pins), encoding="utf-8")
    errors = validate_pipeline_contract(contract, root)["errors"]
    assert any("mutation pins" in error for error in errors), errors


@pytest.mark.parametrize("change,expected", [
    (lambda c: c["contract_amendments"][1].__setitem__("sections", ["17.2"]), "contract amendments"),
    (lambda c: c["optional_policy_profiles"][0].__setitem__("id", "local-pilot-v1"), "optional policy profile"),
    (lambda c: c["optional_policy_profiles"][0].__setitem__("commits", True), "optional policy profile"),
    (lambda c: c["optional_disposition_policies"].__setitem__("values", ["quarantine"]), "disposition policies"),
    (lambda c: c["suite_contract"].__setitem__("statuses", ["ACTIVE"]), "suite contract"),
    (lambda c: c["requirement_identity"].__setitem__("id_pattern_setting", "id_pattern"), "requirement identity"),
])
def test_checker_rejects_changed_wave3_sections(pack_root: Path, change, expected: str) -> None:
    contract = copy.deepcopy(_contract(pack_root))
    change(contract)
    errors = validate_pipeline_contract(contract, pack_root)["errors"]
    assert any(expected in error for error in errors), errors


def test_projections_render_the_wave3_sections_only_with_their_amendment(pack_root: Path) -> None:
    contract = _contract(pack_root)
    for text in _rendered_files(contract).values():
        assert "optional profile `suite-update-v1`" in text
        assert "disposition policy `quarantine`" in text
    without = copy.deepcopy(contract)
    without["contract_amendments"] = [row for row in without["contract_amendments"] if row["wave"] != 3]
    for text in _rendered_files(without).values():
        assert "suite-update-v1" not in text and "disposition policy" not in text
