"""D1c c1: the test design in the living suite — stored by requirement key (SREQ numbers change on an update),
recorded in the manifest with its digest and the design coverage; a suite without a design stays as before."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_suite_manifest import _local, needs_java
from tools import test_design as design_tools
from tools.suite_manifest import read_suite, sha256_bytes, validate, verify


def _identities(numbers: dict[str, str]) -> list[dict[str, Any]]:
    return [{"key": key, "source_requirement_id": sreq} for key, sreq in numbers.items()]


def _design() -> dict[str, Any]:
    design = {"schema_version": "1.0.0", "stage": "test-design", "warnings": [], "artifacts": {
        "parameters": [{"id": "P-001", "name": "роль", "requirement_ids": ["SREQ-0001"], "kind": "enum", "notes": ""},
                       {"id": "P-002", "name": "страница", "requirement_ids": ["SREQ-0001"], "kind": "enum", "notes": ""},
                       {"id": "P-003", "name": "тип", "requirement_ids": ["SREQ-0002"], "kind": "enum", "notes": ""}],
        "partitions": [{"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "админ", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-002", "parameter_id": "P-001", "valid": False, "condition": "гость", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-003", "parameter_id": "P-002", "valid": True, "condition": "первая", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-004", "parameter_id": "P-002", "valid": False, "condition": "нет", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-005", "parameter_id": "P-003", "valid": True, "condition": "dog", "requirement_ids": ["SREQ-0002"]},
                       {"id": "EQ-006", "parameter_id": "P-003", "valid": False, "condition": "dragon", "requirement_ids": ["SREQ-0002"]}],
        "boundaries": [],
        "rules": [{"id": "DR-001", "kind": "error", "conditions": [{"ref": "EQ-006"}], "expected": "ошибка типа", "requirement_ids": ["SREQ-0002"]},
                  {"id": "DR-002", "kind": "decision", "conditions": [{"ref": "EQ-001"}, {"ref": "EQ-005"}], "expected": "сохранено",
                   "requirement_ids": ["SREQ-0001", "SREQ-0002"]}],
        "not_designed": [{"requirement_id": "SREQ-0003", "reason": "контекст"}]}}
    return design_tools.with_combinations(design, design_tools.combinations(design, 16))


def test_a_stored_design_names_requirement_keys_not_sreq_numbers() -> None:
    identities = _identities({"REQ-1": "SREQ-0001", "REQ-2": "SREQ-0002", "REQ-3": "SREQ-0003"})
    stored = design_tools.keyed_design(_design(), identities, full_factorial_max=16, uncovered=[{"ref": "DR-001", "reason": "нет стенда"}])
    assert (stored["format_version"], stored["full_factorial_max"]) == ("1.0.0", 16)
    assert stored["uncovered"] == [{"ref": "DR-001", "reason": "нет стенда"}]
    text = json.dumps(stored, ensure_ascii=False)
    assert "SREQ-" not in text and "REQ-1" in text
    assert stored["design"]["artifacts"]["combinations"][0]["requirement_ids"] == ["REQ-1"]
    assert stored["design"]["artifacts"]["not_designed"] == [{"requirement_id": "REQ-3", "reason": "контекст"}]


def test_a_stored_design_reads_back_with_the_current_sreq_numbers() -> None:
    identities = _identities({"REQ-1": "SREQ-0001", "REQ-2": "SREQ-0002", "REQ-3": "SREQ-0003"})
    stored = design_tools.keyed_design(_design(), identities, full_factorial_max=16)
    renumbered = _identities({"REQ-0": "SREQ-0001", "REQ-1": "SREQ-0002", "REQ-2": "SREQ-0003", "REQ-3": "SREQ-0004"})
    design = design_tools.sreq_design(stored, renumbered)
    assert design["artifacts"]["parameters"][0]["requirement_ids"] == ["SREQ-0002"]
    assert design["artifacts"]["rules"][1]["requirement_ids"] == ["SREQ-0002", "SREQ-0003"]
    assert design["artifacts"]["not_designed"] == [{"requirement_id": "SREQ-0004", "reason": "контекст"}]
    original = _design()
    assert [row["id"] for row in design["artifacts"]["combinations"]] == [row["id"] for row in original["artifacts"]["combinations"]]


def test_a_removed_requirement_leaves_the_design_with_its_elements() -> None:
    identities = _identities({"REQ-1": "SREQ-0001", "REQ-2": "SREQ-0002", "REQ-3": "SREQ-0003"})
    stored = design_tools.keyed_design(_design(), identities, full_factorial_max=16)
    design = design_tools.sreq_design(stored, _identities({"REQ-1": "SREQ-0001", "REQ-3": "SREQ-0002"}))
    artifacts = design["artifacts"]
    # REQ-2 is gone: its parameter, partitions and error rule go; the shared rule keeps REQ-1 and loses its EQ-005.
    assert [row["id"] for row in artifacts["parameters"]] == ["P-001", "P-002"]
    assert [row["id"] for row in artifacts["partitions"]] == ["EQ-001", "EQ-002", "EQ-003", "EQ-004"]
    assert [(row["id"], row["requirement_ids"], row["conditions"]) for row in artifacts["rules"]] == [("DR-002", ["SREQ-0001"], [{"ref": "EQ-001"}])]
    assert all(set(row["partition_ids"]) <= {"EQ-001", "EQ-003"} for row in artifacts["combinations"])
    plain = {**design, "artifacts": {name: rows for name, rows in artifacts.items() if name != "combinations"}}
    assert design_tools.validate_design(plain, ["SREQ-0001", "SREQ-0002"]) == []


# --- the suite of a run --------------------------------------------------------------------------------------------


def _design_on(project: Path) -> None:
    path = project / ".skillsrc"
    path.write_text(path.read_text(encoding="utf-8").replace("design:\n  enabled: false\n", "design:\n  enabled: true\n"), encoding="utf-8")


@needs_java
def test_a_suite_of_a_design_run_stores_the_design_by_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    _design_on(replay.project)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    assert done["result"]["suite"]["status"] == "WRITTEN", done["result"]["suite"]
    manifest = read_suite(replay.project, "test-cases/")
    assert validate(manifest) == []
    row = manifest["suite_files"]["design"]
    stored = (replay.project / row["path"]).read_bytes()
    assert row["path"] == "test-cases/test-design.json" and sha256_bytes(stored) == row["sha256"]
    value = json.loads(stored)
    keys = {item["key"] for item in manifest["requirements"]}
    assert {item["requirement_id"] for item in value["design"]["artifacts"]["not_designed"]} == keys
    assert "SREQ-" not in stored.decode("utf-8")
    assert manifest["design_coverage"] == done["result"]["design_coverage"]
    assert verify(replay.project, manifest)["suite_files"] == []


@needs_java
def test_a_suite_without_a_design_has_no_design_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    manifest = read_suite(replay.project, "test-cases/")
    assert "design" not in manifest["suite_files"] and "design_coverage" not in manifest
    assert not (replay.project / "test-cases" / "test-design.json").exists()
