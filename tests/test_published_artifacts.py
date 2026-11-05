"""V8 (A5-8): ``published_artifacts`` of the contract lists what the code publishes under a run — and ``contract_check`` holds both sides:
a folder of receipts without a row is red (the ratchet), a row whose writer is gone or gives another path is red."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.contract_check import validate_pipeline_contract

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))


def _errors(contract: dict) -> list[str]:
    return [error for error in validate_pipeline_contract(contract, ROOT)["errors"] if "published_artifacts" in error]


def _mutated(contract: dict, change) -> dict:
    document = copy.deepcopy(contract)
    change(document["published_artifacts"])
    return document


def test_the_table_is_what_the_code_publishes(contract: dict) -> None:
    assert _errors(contract) == []


def test_a_folder_of_receipts_without_a_row_is_red(contract: dict) -> None:
    broken = _mutated(contract, lambda rows: rows.__delitem__(next(i for i, row in enumerate(rows) if row.get("kind") == "batch-plan")))
    assert any("folders" in error or "without a row" in error for error in _errors(broken)), _errors(broken)


def test_a_row_whose_writer_is_gone_is_red(contract: dict) -> None:
    def change(rows: list) -> None:
        next(row for row in rows if row.get("path") == "refusals.jsonl")["writer"] = "tools.json_cli:_NO_SUCH_NAME"

    assert any("does not exist" in error for error in _errors(_mutated(contract, change)))


def test_a_row_whose_writer_gives_another_path_is_red(contract: dict) -> None:
    def change(rows: list) -> None:
        next(row for row in rows if row.get("path") == "refusals.jsonl")["path"] = "other.jsonl"

    assert any("not the end of the path" in error for error in _errors(_mutated(contract, change)))


def test_a_row_of_an_unknown_registry_id_is_red(contract: dict) -> None:
    def change(rows: list) -> None:
        next(row for row in rows if row.get("kind") == "batch-plan")["registry_id"] = "no_such_artifact"

    assert any("not in artifact_registry" in error for error in _errors(_mutated(contract, change)))


def test_a_row_without_a_path_has_no_writer_and_says_where_the_artifact_lives(contract: dict) -> None:
    placeless = [row for row in contract["published_artifacts"] if row["path"] is None]
    assert {row.get("registry_id") for row in placeless if row.get("registry_id")} == {
        "skillsrc_proposal_receipt", "normalized_requirements", "pre_review_audit", "reviewer_evidence_transfer", "environment_receipt",
        "orchestrator_output"}
    assert all(row.get("note") and not row.get("writer") and not row.get("kind") for row in placeless)

    def change(rows: list) -> None:
        next(row for row in rows if row.get("registry_id") == "pre_review_audit")["writer"] = "tools.pilot_state:_receipt_target"

    assert any("without a path" in error for error in _errors(_mutated(contract, change)))


def test_context_marker_is_one_call_or_one_plus_n_not_once(contract: dict) -> None:
    row = next(row for row in contract["published_artifacts"] if row.get("stage") == "context-marker")
    assert "1+N" in row["note"]
