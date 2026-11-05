"""V0: which schema files the package has, against the schemas its contract names — the count ``44`` said this by luck."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Schemas that are no artifact of the pipeline and stay out of ``contracts/pipeline.json``; a third one is a decision, not an accident.
OUTSIDE_THE_CONTRACT = frozenset({"project-discovery-output.schema.json", "review-canaries.schema.json"})


def schema_files() -> set[str]:
    return {path.name for path in (ROOT / "schemas").glob("*.json")}


def contract_schema_ids() -> set[str]:
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    return {row["id"] for row in contract["schema_registry"]}


def assert_every_schema_is_in_the_contract_or_the_list(names: set[str] | frozenset[str]) -> None:
    """``names`` — the schemas a test covers: they are exactly the files, and every file is in the registry of the contract or in the list."""
    files = schema_files()
    assert set(names) == files, f"covered but no file: {sorted(set(names) - files)}; file but not covered: {sorted(files - set(names))}"
    registered = contract_schema_ids()
    assert not registered - files, f"in the contract but no file: {sorted(registered - files)}"
    assert not files - registered - OUTSIDE_THE_CONTRACT, (
        f"schema file in neither contracts/pipeline.json schema_registry nor tests/contract/schema_registry.py OUTSIDE_THE_CONTRACT: "
        f"{sorted(files - registered - OUTSIDE_THE_CONTRACT)}")
    assert OUTSIDE_THE_CONTRACT <= files, f"stale entry of OUTSIDE_THE_CONTRACT: {sorted(OUTSIDE_THE_CONTRACT - files)}"
