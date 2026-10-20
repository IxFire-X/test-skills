"""Test design (D1a): the design artifact schema, its validator, the brief and the batch slice, coverage and priorities.

Pure functions of ``tools.test_design``; the stage in the driver and the journal is tested in
``tests/test_test_design_stage.py``.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tools import test_design as design_tools
from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "test-design.schema.json"
REQUIREMENTS = ["SREQ-0001", "SREQ-0002", "SREQ-0003"]


def _design() -> dict[str, Any]:
    """A small valid design: an ordered age (SREQ-0001), a role enum (SREQ-0002), a decision and an error rule
    (SREQ-0002), and SREQ-0003 declared not designable."""
    return {
        "schema_version": "1.0.0",
        "stage": "test-design",
        "artifacts": {
            "parameters": [
                {"id": "P-001", "name": "возраст владельца", "requirement_ids": ["SREQ-0001"], "kind": "ordered", "notes": ""},
                {"id": "P-002", "name": "роль", "requirement_ids": ["SREQ-0002"], "kind": "enum", "notes": "две роли"},
            ],
            "partitions": [
                {"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "18..120", "requirement_ids": ["SREQ-0001"]},
                {"id": "EQ-002", "parameter_id": "P-001", "valid": False, "condition": "меньше 18", "requirement_ids": ["SREQ-0001"]},
                {"id": "EQ-003", "parameter_id": "P-002", "valid": True, "condition": "администратор", "requirement_ids": ["SREQ-0002"]},
                {"id": "EQ-004", "parameter_id": "P-002", "valid": False, "condition": "гость", "requirement_ids": ["SREQ-0002"]},
            ],
            "boundaries": [
                {"id": "BV-001", "partition_id": "EQ-001", "value": "18", "side": "min"},
                {"id": "BV-002", "partition_id": "EQ-002", "value": "17", "side": "just_outside"},
            ],
            "rules": [
                {"id": "DR-001", "kind": "decision", "conditions": [{"ref": "EQ-003"}, {"text": "владелец существует"}],
                 "expected": "карточка владельца открыта", "requirement_ids": ["SREQ-0002"]},
                {"id": "DR-002", "kind": "error", "conditions": [{"ref": "EQ-004"}], "expected": "ответ 403", "requirement_ids": ["SREQ-0002"]},
            ],
            "not_designed": [{"requirement_id": "SREQ-0003", "reason": "нефункциональное требование без входных параметров"}],
        },
        "warnings": [],
    }


def _codes(design: dict[str, Any], requirements: list[str] = REQUIREMENTS) -> set[str]:
    return {row["code"] for row in design_tools.validate_design(design, requirements)}


def _schema(value: Any) -> list[dict[str, str]]:
    return schema_diagnostics(value, SCHEMA, ROOT)


# --- schema -------------------------------------------------------------------------------------------------------


def test_a_minimal_design_is_valid() -> None:
    assert _schema(_design()) == []
    assert design_tools.validate_design(_design(), REQUIREMENTS) == []


def test_the_schema_is_closed() -> None:
    value = _design()
    value["artifacts"]["parameters"][0]["priority"] = "HIGH"
    assert _schema(value)
    value = _design()
    value["extra"] = 1
    assert _schema(value)


@pytest.mark.parametrize("field, index, bad", [("parameters", 0, "P-1"), ("partitions", 0, "EQ-01a"), ("boundaries", 0, "B-001"), ("rules", 0, "DR001")])
def test_an_id_of_another_format_is_refused_by_the_schema(field: str, index: int, bad: str) -> None:
    value = _design()
    value["artifacts"][field][index]["id"] = bad
    assert _schema(value)


def test_a_condition_is_a_ref_or_a_text_object_not_a_bare_string() -> None:
    value = _design()
    value["artifacts"]["rules"][0]["conditions"] = ["EQ-003"]
    assert _schema(value)
    value["artifacts"]["rules"][0]["conditions"] = [{"ref": "EQ-003", "text": "и ещё"}]
    assert _schema(value)


def test_kinds_and_sides_are_closed_lists() -> None:
    value = _design()
    value["artifacts"]["parameters"][0]["kind"] = "number"
    assert _schema(value)
    value = _design()
    value["artifacts"]["boundaries"][0]["side"] = "edge"
    assert _schema(value)


# --- validator ----------------------------------------------------------------------------------------------------


def test_an_id_used_twice_even_across_arrays_is_a_duplicate() -> None:
    value = _design()
    value["artifacts"]["partitions"][1]["id"] = "EQ-001"
    assert "DESIGN_ID_DUPLICATE" in _codes(value)


@pytest.mark.parametrize("mutate", [
    lambda d: d["artifacts"]["partitions"][0].__setitem__("parameter_id", "P-009"),
    lambda d: d["artifacts"]["boundaries"][0].__setitem__("partition_id", "EQ-009"),
    lambda d: d["artifacts"]["rules"][0]["conditions"].__setitem__(0, {"ref": "EQ-009"}),
    lambda d: d["artifacts"]["rules"][0]["conditions"].__setitem__(0, {"ref": "BV-009"}),
])
def test_a_reference_to_a_missing_element_is_unknown(mutate: Any) -> None:
    value = _design()
    mutate(value)
    assert "DESIGN_REF_UNKNOWN" in _codes(value)


def test_a_reference_to_an_element_of_the_wrong_kind_is_unknown() -> None:
    value = _design()
    value["artifacts"]["partitions"][0]["parameter_id"] = "EQ-002"
    assert "DESIGN_REF_UNKNOWN" in _codes(value)
    value = _design()
    value["artifacts"]["boundaries"][0]["partition_id"] = "P-001"
    assert "DESIGN_REF_UNKNOWN" in _codes(value)
    value = _design()
    value["artifacts"]["rules"][0]["conditions"][0] = {"ref": "DR-002"}
    assert "DESIGN_REF_UNKNOWN" in _codes(value)


@pytest.mark.parametrize("mutate", [
    lambda d: d["artifacts"]["parameters"][0]["requirement_ids"].append("SREQ-0099"),
    lambda d: d["artifacts"]["rules"][0]["requirement_ids"].__setitem__(0, "SREQ-0099"),
    lambda d: d["artifacts"]["not_designed"][0].__setitem__("requirement_id", "SREQ-0099"),
])
def test_a_requirement_the_marker_did_not_find_is_unknown(mutate: Any) -> None:
    value = _design()
    mutate(value)
    assert "DESIGN_REQUIREMENT_UNKNOWN" in _codes(value)


def test_every_requirement_is_designed_or_declared_not_designed() -> None:
    value = _design()
    value["artifacts"]["not_designed"] = []
    rows = design_tools.validate_design(value, REQUIREMENTS)
    assert [(row["code"], "SREQ-0003" in row["message"]) for row in rows] == [("DESIGN_REQUIREMENT_UNCOVERED", True)]


def test_a_requirement_declared_not_designed_must_not_be_designed() -> None:
    value = _design()
    value["artifacts"]["not_designed"].append({"requirement_id": "SREQ-0001", "reason": "не разбивается"})
    assert "DESIGN_NOT_DESIGNED_CONFLICT" in _codes(value)


def test_a_parameter_without_partitions_is_refused() -> None:
    value = _design()
    value["artifacts"]["parameters"].append({"id": "P-003", "name": "имя", "requirement_ids": ["SREQ-0001"], "kind": "text", "notes": ""})
    assert "DESIGN_PARAMETER_UNPARTITIONED" in _codes(value)


def test_a_parameter_needs_a_valid_and_an_invalid_partition() -> None:
    value = _design()
    value["artifacts"]["partitions"] = [row for row in value["artifacts"]["partitions"] if row["id"] != "EQ-004"]
    value["artifacts"]["rules"] = [row for row in value["artifacts"]["rules"] if row["id"] != "DR-002"]
    assert _codes(value) == {"DESIGN_PARAMETER_ONE_SIDED"}


def test_a_reason_for_a_single_class_lifts_the_one_sided_refusal() -> None:
    value = _design()
    value["artifacts"]["partitions"] = [row for row in value["artifacts"]["partitions"] if row["id"] != "EQ-004"]
    value["artifacts"]["rules"] = [row for row in value["artifacts"]["rules"] if row["id"] != "DR-002"]
    value["artifacts"]["parameters"][1]["single_class_reason"] = "роль задаётся системой, неверной роли не бывает"
    assert _schema(value) == []
    assert _codes(value) == set()


def test_an_ordered_parameter_needs_a_boundary() -> None:
    value = _design()
    value["artifacts"]["boundaries"] = []
    assert _codes(value) == {"DESIGN_ORDERED_WITHOUT_BOUNDARY"}


def test_a_boundary_belongs_only_to_an_ordered_parameter() -> None:
    value = _design()
    value["artifacts"]["boundaries"].append({"id": "BV-003", "partition_id": "EQ-003", "value": "admin", "side": "min"})
    assert _codes(value) == {"DESIGN_BOUNDARY_NOT_ORDERED"}


@pytest.mark.parametrize("mutate", [
    lambda d: d["artifacts"]["rules"][0].__setitem__("expected", "   "),
    lambda d: d["artifacts"]["partitions"][0].__setitem__("condition", " \t"),
    lambda d: d["artifacts"]["rules"][0]["conditions"].__setitem__(1, {"text": "  "}),
    lambda d: d["artifacts"]["not_designed"][0].__setitem__("reason", " "),
])
def test_a_blank_text_is_refused(mutate: Any) -> None:
    value = _design()
    mutate(value)
    assert "DESIGN_TEXT_BLANK" in _codes(value)


def test_a_design_where_nothing_is_designed_only_warns() -> None:
    value = _design()
    value["artifacts"].update(parameters=[], partitions=[], boundaries=[], rules=[],
                              not_designed=[{"requirement_id": item, "reason": "нет входов"} for item in REQUIREMENTS])
    assert design_tools.validate_design(value, REQUIREMENTS) == []
    assert any("DESIGN_EMPTY" in line for line in design_tools.design_warnings(value))


def test_validator_rows_carry_rfc6901_paths() -> None:
    value = _design()
    value["artifacts"]["boundaries"][1]["partition_id"] = "EQ-009"
    rows = design_tools.validate_design(value, REQUIREMENTS)
    assert [(row["path"], row["code"]) for row in rows] == [("/artifacts/boundaries/1/partition_id", "DESIGN_REF_UNKNOWN")]
    assert all(set(row) == {"path", "code", "message"} for row in rows)


# --- brief and slice ----------------------------------------------------------------------------------------------


def _marker_requirements() -> list[dict[str, Any]]:
    return [
        {"source_requirement_id": "SREQ-0002", "display_order": 2, "text": "Роли", "provenance": ["docs/a.md"], "digest": "sha256:" + "2" * 64},
        {"source_requirement_id": "SREQ-0001", "display_order": 1, "text": "Возраст", "provenance": ["docs/a.md"], "digest": "sha256:" + "1" * 64},
    ]


def test_the_brief_carries_only_requirement_text_in_display_order() -> None:
    brief = design_tools.design_brief(_marker_requirements())
    assert [row["source_requirement_id"] for row in brief["requirements"]] == ["SREQ-0001", "SREQ-0002"]
    assert set(brief["requirements"][0]) == {"source_requirement_id", "display_order", "text"}
    assert design_tools.canonical_bytes(brief) == design_tools.canonical_bytes(design_tools.design_brief(list(reversed(_marker_requirements()))))
    assert design_tools.digest(brief).startswith("sha256:")


def _ids(value: dict[str, Any]) -> dict[str, list[str]]:
    return {name: [row.get("id", row.get("requirement_id")) for row in value["artifacts"][name]]
            for name in ("parameters", "partitions", "boundaries", "rules", "not_designed")}


def test_a_slice_keeps_the_elements_of_owned_requirements_with_their_parameters() -> None:
    sliced = design_tools.design_slice(_design(), ["SREQ-0001"])
    assert _ids(sliced) == {"parameters": ["P-001"], "partitions": ["EQ-001", "EQ-002"], "boundaries": ["BV-001", "BV-002"],
                            "rules": [], "not_designed": []}


def test_a_rule_pulls_the_parameter_of_every_partition_it_names() -> None:
    value = _design()
    value["artifacts"]["rules"][0]["conditions"].append({"ref": "BV-002"})
    sliced = design_tools.design_slice(value, ["SREQ-0002"])
    assert _ids(sliced)["parameters"] == ["P-001", "P-002"]
    assert _ids(sliced)["boundaries"] == ["BV-001", "BV-002"]
    assert _ids(sliced)["rules"] == ["DR-001", "DR-002"]


def test_a_slice_without_owned_design_is_empty_and_deterministic() -> None:
    sliced = design_tools.design_slice(_design(), ["SREQ-0003"])
    assert _ids(sliced) == {"parameters": [], "partitions": [], "boundaries": [], "rules": [], "not_designed": ["SREQ-0003"]}
    assert design_tools.canonical_bytes(sliced) == design_tools.canonical_bytes(design_tools.design_slice(copy.deepcopy(_design()), ["SREQ-0003"]))


# --- the generator's references -----------------------------------------------------------------------------------


def _case(case_id: str, refs: list[str], categories: list[str] | None = None) -> dict[str, Any]:
    return {"case_id": case_id, "categories": categories or ["positive"], "design_refs": refs}


def _covering_cases() -> list[dict[str, Any]]:
    return [
        _case("TC-B1-001", ["EQ-001", "BV-001"]),
        _case("TC-B1-002", ["EQ-002", "BV-002"], ["negative", "boundary"]),
        _case("TC-B1-003", ["DR-001", "EQ-003"]),
        _case("TC-B1-004", ["DR-002", "EQ-004"], ["negative"]),
    ]


def _fragment_codes(cases: list[dict[str, Any]], uncovered: list[dict[str, str]] | None = None) -> set[str]:
    return {row["code"] for row in design_tools.fragment_design_problems(_design(), cases, uncovered or [])}


def test_cases_covering_the_slice_have_no_problems() -> None:
    assert design_tools.fragment_design_problems(_design(), _covering_cases(), []) == []


def test_a_case_without_design_refs_is_refused() -> None:
    cases = _covering_cases() + [_case("TC-B1-005", [])]
    assert _fragment_codes(cases) == {"DESIGN_REFS_MISSING"}
    del cases[-1]["design_refs"]
    assert _fragment_codes(cases) == {"DESIGN_REFS_MISSING"}


def test_a_reference_outside_the_slice_is_unknown() -> None:
    cases = _covering_cases()
    cases[0]["design_refs"].append("EQ-077")
    assert _fragment_codes(cases) == {"DESIGN_REF_UNKNOWN"}
    assert _fragment_codes(_covering_cases(), [{"ref": "DR-077", "reason": "нет"}]) == {"DESIGN_REF_UNKNOWN"}


def test_an_element_neither_covered_nor_declared_uncovered_is_refused() -> None:
    cases = [case for case in _covering_cases() if case["case_id"] != "TC-B1-003"]
    assert _fragment_codes(cases) == {"DESIGN_ELEMENT_UNCOVERED"}
    assert _fragment_codes(cases, [{"ref": "DR-001", "reason": "нужен второй владелец"}, {"ref": "EQ-003", "reason": "то же"}]) == set()


def test_an_element_covered_and_declared_uncovered_is_a_conflict() -> None:
    assert _fragment_codes(_covering_cases(), [{"ref": "EQ-001", "reason": "нет"}]) == {"DESIGN_UNCOVERED_CONFLICT"}


def test_an_invalid_partition_needs_a_negative_case() -> None:
    cases = _covering_cases()
    cases[3]["categories"] = ["positive"]
    assert _fragment_codes(cases) == {"DESIGN_NEGATIVE_MISSING"}


# --- coverage -----------------------------------------------------------------------------------------------------


def test_coverage_counts_each_element_kind() -> None:
    cases = [case for case in _covering_cases() if case["case_id"] != "TC-B1-004"]
    result = design_tools.coverage(_design(), cases, [{"ref": "DR-002", "reason": "нужна вторая роль"}])
    assert result["partitions"] == {"total": 4, "covered": 3, "uncovered": 0, "ratio": 0.75}
    assert result["boundaries"] == {"total": 2, "covered": 2, "uncovered": 0, "ratio": 1.0}
    assert result["rules"] == {"total": 2, "covered": 1, "uncovered": 1, "ratio": 0.5}
    assert result["negative_partitions"] == {"total": 2, "covered": 1, "uncovered": 0, "ratio": 0.5}
    assert result["uncovered"] == [{"ref": "DR-002", "reason": "нужна вторая роль"}]


def test_an_element_covered_by_any_batch_is_covered() -> None:
    uncovered = [{"ref": "EQ-001", "reason": "в другом пакете"}]
    result = design_tools.coverage(_design(), _covering_cases(), uncovered)
    assert result["partitions"]["covered"] == 4 and result["partitions"]["uncovered"] == 0
    assert result["uncovered"] == []


def test_an_empty_kind_has_no_ratio() -> None:
    value = _design()
    value["artifacts"]["boundaries"] = []
    assert design_tools.coverage(value, [], [])["boundaries"] == {"total": 0, "covered": 0, "uncovered": 0, "ratio": None}


# --- priorities ---------------------------------------------------------------------------------------------------


def test_priorities_follow_the_table_in_document_order() -> None:
    cases = [
        _case("TC-1", ["DR-002"], ["negative"]),            # error rule -> HIGH
        _case("TC-2", ["EQ-004"], ["negative"]),            # invalid partition -> HIGH
        _case("TC-3", ["BV-002"], ["boundary"]),            # just outside -> HIGH
        _case("TC-4", ["BV-001"], ["boundary"]),            # other boundary -> MEDIUM
        _case("TC-5", ["DR-001", "EQ-003"]),                # first main scenario -> MEDIUM
        _case("TC-6", ["EQ-003"]),                          # repeats a covered valid element -> LOW
        _case("TC-7", ["EQ-001"]),                          # first case on EQ-001 -> MEDIUM
        _case("TC-8", ["DR-001"]),                          # repeat -> LOW
    ]
    assert design_tools.assign_priorities(_design(), cases) == ["HIGH", "HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW", "MEDIUM", "LOW"]


def test_a_boundary_of_an_invalid_partition_is_high_whatever_its_side() -> None:
    value = _design()
    value["artifacts"]["boundaries"][1]["side"] = "max"
    assert design_tools.assign_priorities(value, [_case("TC-1", ["BV-002"], ["boundary"])]) == ["HIGH"]


def test_critical_is_never_assigned() -> None:
    cases = _covering_cases() * 3
    assert "CRITICAL" not in design_tools.assign_priorities(_design(), cases)


def test_a_degenerate_distribution_warns_with_the_counts() -> None:
    assert design_tools.priority_warning({"CRITICAL": 0, "HIGH": 7, "MEDIUM": 3, "LOW": 0}) is None
    warning = design_tools.priority_warning({"CRITICAL": 0, "HIGH": 71, "MEDIUM": 29, "LOW": 0})
    assert warning is not None and "HIGH" in warning and "71" in warning and "100" in warning
    assert design_tools.priority_warning({"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}) is None


def test_priority_counts_cover_every_level() -> None:
    assert design_tools.priority_counts([{"priority": "HIGH"}, {"priority": "LOW"}, {"priority": "HIGH"}]) == {
        "CRITICAL": 0, "HIGH": 2, "MEDIUM": 0, "LOW": 1}


# --- .skillsrc ----------------------------------------------------------------------------------------------------


def test_design_is_on_unless_skillsrc_turns_it_off() -> None:
    assert design_tools.design_enabled({}) is True
    assert design_tools.design_enabled({"design": {"full_factorial_max": 8}}) is True
    assert design_tools.design_enabled({"design": {"enabled": True}}) is True
    assert design_tools.design_enabled({"design": {"enabled": False}}) is False


def test_the_skillsrc_design_section_is_closed() -> None:
    import json

    from tools.skillsrc_manifest import parse_skillsrc_bytes

    base = json.loads((ROOT / "tests" / "fixtures" / "projects" / "python-pytest" / ".skillsrc").read_text(encoding="utf-8"))
    for section in ({"enabled": False}, {"enabled": True, "full_factorial_max": 16}):
        assert parse_skillsrc_bytes(json.dumps({**base, "design": section}).encode("utf-8"))["design"] == section
    for section in ({"enabled": "no"}, {"full_factorial_max": 0}, {"pairwise": True}):
        with pytest.raises(Exception):
            parse_skillsrc_bytes(json.dumps({**base, "design": section}).encode("utf-8"))
