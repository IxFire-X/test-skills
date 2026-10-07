"""Wave 2 contract amendments (2026-10-07): opt-in sections next to the frozen contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.contract_check import validate_pipeline_contract
from tools.render_contract_docs import _rendered_files


def _contract(root: Path) -> dict:
    return json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))


def test_amended_contract_passes_and_keeps_frozen_sections(pack_root: Path) -> None:
    contract = _contract(pack_root)
    assert validate_pipeline_contract(contract, pack_root, check_drift=True) == {"status": "passed", "errors": []}
    # The frozen lifecycle is untouched: MUTATION is an optional stage between two adjacent frozen stages.
    assert "MUTATION" not in contract["physical_lifecycle"]
    [stage] = contract["optional_lifecycle_stages"]
    lifecycle = contract["physical_lifecycle"]
    assert lifecycle.index(stage["before"]) == lifecycle.index(stage["after"]) + 1 == lifecycle.index("RETAIN_OR_CLEANUP_DECISION")
    assert set(contract["optional_result_axes"]) == {"test_strength", "isolation_evidence"}
    assert not set(contract["optional_result_axes"]) & set(contract["result_axes"])
    [amendment] = [row for row in contract["contract_amendments"] if row["wave"] == 2]
    assert (pack_root / amendment["document"]).is_file() and amendment["opt_in"] is True


@pytest.mark.parametrize("change,expected", [
    (lambda c: c["contract_amendments"][0].__setitem__("sections", ["1.2"]), "contract amendments"),
    (lambda c: c["contract_amendments"][0].__setitem__("wave", 3), "contract amendments"),
    (lambda c: c["optional_lifecycle_stages"][0].__setitem__("before", "DISPOSITION_RECEIPTS"), "optional lifecycle stage"),
    (lambda c: c["optional_result_axes"]["test_strength"]["values"].append("FAIL"), "optional result axes"),
    (lambda c: c["mutation_tooling"]["java"].__setitem__("version", "1.29.0"), "mutation tooling"),
    (lambda c: c["model_runner"].__setitem__("custom_template", "skillsrc"), "model runner"),
])
def test_checker_rejects_changed_amendments(pack_root: Path, change, expected: str) -> None:
    contract = copy.deepcopy(_contract(pack_root))
    change(contract)
    errors = validate_pipeline_contract(contract, pack_root)["errors"]
    assert any(expected in error for error in errors), errors


def test_projections_render_the_amendments(pack_root: Path) -> None:
    rendered = _rendered_files(_contract(pack_root))
    for text in rendered.values():
        assert "optional stage `MUTATION` between `EXECUTION_TRACE` and `RETAIN_OR_CLEANUP_DECISION`" in text
        assert "`--require-driver-isolation` rejects lower levels with `REVIEW_ISOLATION_UNVERIFIED`" in text
    # A contract without the amendments renders exactly as before (no empty section).
    frozen = {key: value for key, value in _contract(pack_root).items()
              if key not in {"contract_amendments", "optional_lifecycle_stages", "optional_result_axes", "mutation_tooling", "model_runner",
                            "optional_schema_registry", "optional_artifact_registry", "optional_skills", "optional_stage_registry"}}
    assert all("amendments" not in text for text in _rendered_files(frozen).values())


def test_full_check_requires_the_amendment_document(pack_root: Path) -> None:
    contract = copy.deepcopy(_contract(pack_root))
    contract["contract_amendments"][0]["document"] = "docs/superpowers/specs/missing.md"
    errors = validate_pipeline_contract(contract, pack_root, check_drift=True)["errors"]
    assert "amendment document missing: docs/superpowers/specs/missing.md" in errors
