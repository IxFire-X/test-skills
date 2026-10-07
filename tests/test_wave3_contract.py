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
    assert policies["defaults"] == {"local-pilot-v1": "cleanup", "suite-update-v1": "quarantine"}
    assert policies["quarantine"]["never_changes"] == ["verification", "coverage", "accepted", "result_tuple"]
    assert contract["suite_contract"]["default_path"] == "test-cases/"
    assert contract["requirement_identity"]["sreq_format"] == "unchanged"


@pytest.mark.parametrize("change,expected", [
    (lambda c: c["contract_amendments"][1].__setitem__("sections", ["17.2"]), "contract amendments"),
    (lambda c: c["optional_policy_profiles"][0].__setitem__("id", "local-pilot-v1"), "optional policy profile"),
    (lambda c: c["optional_policy_profiles"][0].__setitem__("commits", True), "optional policy profile"),
    (lambda c: c["optional_disposition_policies"]["defaults"].__setitem__("local-pilot-v1", "quarantine"), "disposition policies"),
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
