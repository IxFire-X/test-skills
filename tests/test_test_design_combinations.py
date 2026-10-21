"""D1b step 7: combinations (``CB-NNN``) of the valid partitions of parameters that share a requirement — computed by
code from the design and ``design.full_factorial_max`` (a fact of the run), covered by the generator, counted in the
design coverage."""
from __future__ import annotations

import copy
import itertools
import json
import random
from pathlib import Path
from typing import Any

import pytest

from tests.test_review_fixes_driver import _drive, _project, _run_root, _start
from tests.test_test_design_generation import Generation, _cases
from tests.test_test_design_stage import _authorization, _design_setting, _request
from tools import test_design as design_tools
from tools.pilot_state import create_run

SREQ = ["SREQ-0001", "SREQ-0002"]


def _parameter(identifier: str, requirement: str, kind: str = "enum") -> dict[str, Any]:
    return {"id": identifier, "name": identifier, "requirement_ids": [requirement], "kind": kind, "notes": ""}


def _partition(identifier: str, parameter: str, valid: bool, requirement: str) -> dict[str, Any]:
    return {"id": identifier, "parameter_id": parameter, "valid": valid, "condition": identifier, "requirement_ids": [requirement]}


def _design(sizes: tuple[int, ...] = (3, 3, 3)) -> dict[str, Any]:
    """Parameters P-001.. of SREQ-0001 with ``sizes`` valid partitions each and one invalid; P-009 alone on SREQ-0002."""
    parameters, partitions, number = [], [], 0
    for index, size in enumerate(sizes, 1):
        parameters.append(_parameter(f"P-{index:03d}", "SREQ-0001"))
        for valid in [True] * size + [False]:
            number += 1
            partitions.append(_partition(f"EQ-{number:03d}", f"P-{index:03d}", valid, "SREQ-0001"))
    parameters.append(_parameter("P-009", "SREQ-0002"))
    partitions += [_partition("EQ-901", "P-009", True, "SREQ-0002"), _partition("EQ-902", "P-009", False, "SREQ-0002")]
    return {"schema_version": "1.0.0", "stage": "test-design", "warnings": [],
            "artifacts": {"parameters": parameters, "partitions": partitions, "boundaries": [], "rules": [], "not_designed": []}}


def _valid(design: dict[str, Any], parameter: str) -> list[str]:
    return [row["id"] for row in design["artifacts"]["partitions"] if row["parameter_id"] == parameter and row["valid"]]


# --- the combinations of a design ---------------------------------------------------------------------------------


def test_combinations_cover_every_pair_of_valid_partitions_of_parameters_sharing_a_requirement() -> None:
    design = _design()
    assert design_tools.validate_design(design, SREQ) == []
    combinations = design_tools.combinations(design, 16)
    assert [row["id"] for row in combinations] == [f"CB-{index:03d}" for index in range(1, len(combinations) + 1)]
    assert 9 <= len(combinations) <= 10  # pairwise, not the 27 of the full product
    assert all(row["requirement_ids"] == ["SREQ-0001"] for row in combinations)
    factors = [_valid(design, parameter) for parameter in ("P-001", "P-002", "P-003")]
    rows = [tuple(row["partition_ids"]) for row in combinations]
    pairs = {(a, b) for x, y in itertools.combinations(factors, 2) for a in x for b in y}
    assert pairs <= {(row[i], row[j]) for row in rows for i, j in itertools.combinations(range(3), 2)}
    # Invalid partitions are tested one at a time by negative cases, never inside a combination.
    invalid = {row["id"] for row in design["artifacts"]["partitions"] if not row["valid"]}
    assert not invalid & {item for row in rows for item in row}


def test_a_product_within_the_threshold_is_combined_in_full() -> None:
    combinations = design_tools.combinations(_design((2, 3)), 16)
    assert len(combinations) == 6
    assert design_tools.combinations(_design((3, 3, 3)), 27).__len__() == 27


def test_a_requirement_with_one_parameter_has_no_combinations() -> None:
    design = _design((3,))
    assert design_tools.combinations(design, 16) == []


def test_combinations_do_not_depend_on_the_order_of_the_input() -> None:
    design = _design((3, 2, 4))
    expected = design_tools.combinations(design, 4)
    shuffled = copy.deepcopy(design)
    generator = random.Random(7)
    for _ in range(5):
        generator.shuffle(shuffled["artifacts"]["parameters"])
        generator.shuffle(shuffled["artifacts"]["partitions"])
        assert design_tools.combinations(shuffled, 4) == expected


def test_a_parameter_of_two_requirements_joins_both_groups_without_duplicates() -> None:
    design = _design((2, 2))
    design["artifacts"]["parameters"][0]["requirement_ids"] = ["SREQ-0001", "SREQ-0002"]
    design["artifacts"]["parameters"][1]["requirement_ids"] = ["SREQ-0001", "SREQ-0002"]
    design["artifacts"]["parameters"][2]["requirement_ids"] = ["SREQ-0003"]
    for row in design["artifacts"]["partitions"]:
        if row["parameter_id"] == "P-009":
            row["requirement_ids"] = ["SREQ-0003"]
    combinations = design_tools.combinations(design, 16)
    # P-001 x P-002 is the same group for both requirements: combined once, naming both.
    assert len(combinations) == 4
    assert {tuple(row["requirement_ids"]) for row in combinations} == {("SREQ-0001", "SREQ-0002")}


# --- slice, references, coverage and priorities -------------------------------------------------------------------


def test_a_slice_carries_the_combinations_of_its_requirements() -> None:
    design = _design((2, 2))
    combinations = design_tools.combinations(design, 16)
    sliced = design_tools.design_slice(design, ["SREQ-0001"], combinations)
    assert [row["id"] for row in sliced["artifacts"]["combinations"]] == [row["id"] for row in combinations]
    assert design_tools.design_slice(design, ["SREQ-0002"], combinations)["artifacts"]["combinations"] == []
    # Without combinations the slice is the D1a slice (its digest binds D1a runs).
    assert "combinations" not in design_tools.design_slice(design, ["SREQ-0001"])["artifacts"]


def _cases_for(sliced: dict[str, Any], combination_ids: list[str]) -> list[dict[str, Any]]:
    artifacts = sliced["artifacts"]
    valid = [row["id"] for row in artifacts["partitions"] if row["valid"]]
    invalid = [row["id"] for row in artifacts["partitions"] if not row["valid"]]
    return [{"case_id": "TC-1", "categories": ["positive"], "design_refs": valid},
            {"case_id": "TC-2", "categories": ["negative"], "design_refs": invalid},
            *({"case_id": f"TC-CB-{ref}", "categories": ["positive"], "design_refs": [ref]} for ref in combination_ids)]


def test_every_combination_is_covered_or_declared_uncovered() -> None:
    design = _design((2, 2))
    combinations = design_tools.combinations(design, 16)
    sliced = design_tools.design_slice(design, ["SREQ-0001", "SREQ-0002"], combinations)
    ids = [row["id"] for row in combinations]
    assert design_tools.fragment_design_problems(sliced, _cases_for(sliced, ids), []) == []
    rows = design_tools.fragment_design_problems(sliced, _cases_for(sliced, ids[1:]), [])
    assert [(row["code"], ids[0] in row["message"]) for row in rows] == [("DESIGN_ELEMENT_UNCOVERED", True)]
    assert design_tools.fragment_design_problems(sliced, _cases_for(sliced, ids[1:]), [{"ref": ids[0], "reason": "нет данных"}]) == []
    coverage = design_tools.coverage(sliced, _cases_for(sliced, ids[1:]), [{"ref": ids[0], "reason": "нет данных"}])
    assert coverage["combinations"] == {"total": 4, "covered": 3, "uncovered": 1, "ratio": 0.75}


def test_the_first_case_on_a_combination_is_medium_and_a_repeat_low() -> None:
    design = _design((2, 2))
    combinations = design_tools.combinations(design, 16)
    sliced = design_tools.design_slice(design, ["SREQ-0001"], combinations)
    first = combinations[0]["id"]
    cases = [{"case_id": "TC-1", "design_refs": [first]}, {"case_id": "TC-2", "design_refs": [first]}]
    assert design_tools.assign_priorities(sliced, cases) == ["MEDIUM", "LOW"]


# --- the run: authorization, driver, journal ----------------------------------------------------------------------


def test_the_threshold_is_a_fact_of_a_design_run(tmp_path: Path) -> None:
    project = _project(tmp_path / "default", local=False)
    _design_setting(project, True)
    task = _drive(project, _start(project, "cases-only-v1"), Generation("cases-only-v1"), until=lambda task: task.get("stage") == "test-design:baseline")
    assert _authorization(_run_root(project, task))["design_full_factorial_max"] == 16

    project = _project(tmp_path / "set", local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["design"] = {"enabled": True, "full_factorial_max": 4}
    (project / ".skillsrc").write_text(json.dumps(skillsrc), encoding="utf-8")
    task = _drive(project, _start(project, "cases-only-v1"), Generation("cases-only-v1"), until=lambda task: task.get("stage") == "test-design:baseline")
    assert _authorization(_run_root(project, task))["design_full_factorial_max"] == 4

    project = _project(tmp_path / "off", local=False)
    _design_setting(project, False)
    task = _drive(project, _start(project, "cases-only-v1"), Generation("cases-only-v1"), until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    assert "design_full_factorial_max" not in _authorization(_run_root(project, task))


@pytest.mark.parametrize("authorization", [
    {"design_full_factorial_max": 16},                              # without design_required
    {"design_required": True, "design_full_factorial_max": 0},      # out of range
    {"design_required": True, "design_full_factorial_max": True},   # not an integer
])
def test_the_threshold_is_refused_outside_a_design_run_or_its_range(tmp_path: Path, authorization: dict[str, Any]) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError):
        create_run(project, "cases-only-v1", {"request_id": "request-1", "execution_requested": False, **authorization})


def _two_parameters(value: dict[str, Any]) -> None:
    """Sort order (2 valid, 1 invalid) and page (2 valid, 1 invalid) of the list requirement: 4 combinations in full."""
    source = value["artifacts"]["rules"][0]["requirement_ids"]
    value["artifacts"]["parameters"] = [
        {"id": "P-001", "name": "сортировка", "requirement_ids": source, "kind": "enum", "notes": ""},
        {"id": "P-002", "name": "страница", "requirement_ids": source, "kind": "enum", "notes": ""},
    ]
    value["artifacts"]["partitions"] = [
        {"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "по имени", "requirement_ids": source},
        {"id": "EQ-002", "parameter_id": "P-001", "valid": True, "condition": "по цене", "requirement_ids": source},
        {"id": "EQ-003", "parameter_id": "P-001", "valid": False, "condition": "неизвестное поле", "requirement_ids": source},
        {"id": "EQ-004", "parameter_id": "P-002", "valid": True, "condition": "первая", "requirement_ids": source},
        {"id": "EQ-005", "parameter_id": "P-002", "valid": True, "condition": "последняя", "requirement_ids": source},
        {"id": "EQ-006", "parameter_id": "P-002", "valid": False, "condition": "за пределами", "requirement_ids": source},
    ]


def _negative_list_case(value: dict[str, Any], _slice: dict[str, Any]) -> None:
    for case in value["test_cases"]:
        if "EQ-003" in case.get("design_refs", []):
            case["categories"] = ["negative"]


def test_the_generator_covers_the_combinations_and_the_result_counts_them(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    model = Generation("cases-only-v1", design=_two_parameters, generator=_negative_list_case)
    done = _drive(project, _start(project, "cases-only-v1"), model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    [task] = model.generator_tasks
    sliced = json.loads(Path(next(path for path in task["inputs"] if "test-design-BATCH-" in path)).read_text(encoding="utf-8"))
    assert [row["partition_ids"] for row in sliced["artifacts"]["combinations"]] == [
        ["EQ-001", "EQ-004"], ["EQ-001", "EQ-005"], ["EQ-002", "EQ-004"], ["EQ-002", "EQ-005"]]
    assert any("CB-" in rule for rule in json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))["design_rules"])
    assert done["result"]["design_coverage"]["combinations"] == {"total": 4, "covered": 4, "uncovered": 0, "ratio": 1.0}
    assert any("CB-001" in case["design_refs"] for case in _cases(project, done))
    # The journal recomputed the slice with the combinations: the generator's fifth input is its digest.
    run_root, attempt_id = _run_root(project, done), done["result"]["attempt_id"]
    generator = _request(run_root, attempt_id, task["stage"])
    assert generator["input_digests"][4] == design_tools.digest(sliced)
