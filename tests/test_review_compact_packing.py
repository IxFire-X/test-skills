"""Р7: compact packing covers every pair of cases, owns every case once and respects the budget.

Random documents of up to 200 cases with random case sizes and budgets:

* every pair of cases meets in at least one part (or a part holding it is explicitly
  blocked because a single case does not fit);
* every case has exactly one home part (one ``local-`` area in the whole plan);
* no part is over the budget unless it is blocked with ``REVIEW_CONTEXT_LIMIT``;
* the plan is deterministic.

Block locality (how many blocks one inserted or deleted case changes) is measured by
``evals/review-scaling/locality.py`` and reported; see the plan, Р7.
"""
from __future__ import annotations

import itertools
import random

import pytest

from tests.review_scaling_helpers import petclinic_snapshot, step5_snapshot, synthetic_document, synthetic_snapshot
from tools.review_modes import build_offline_plan


def _plan(snapshot: dict, budget: int, reserve: int = 2_000) -> dict:
    return build_offline_plan(snapshot, mode="compact-v1", input_byte_budget=budget, response_reserve_bytes=reserve)


def _check(plan: dict, case_ids: list[str]) -> None:
    budget, reserve = plan["input_byte_budget"], plan["snapshot"]["response_reserve_bytes"]
    homes = [area["targets"][0] for part in plan["parts"] for area in part["areas"] if area["kind"] == "local"]
    assert sorted(homes) == sorted(case_ids), "every case needs exactly one home area"
    blocked = set()
    for part in plan["parts"]:
        if part["blocked_reason"] is None:
            assert part["input_byte_count"] + reserve <= budget
        else:
            assert part["blocked_reason"] == "REVIEW_CONTEXT_LIMIT" and part["input_byte_count"] + reserve > budget
            blocked |= set(part["case_ids"])
    together = set()
    for part in plan["parts"]:
        if part["blocked_reason"] is None:
            together |= set(itertools.combinations(sorted(part["case_ids"]), 2))
    missing = [pair for pair in itertools.combinations(sorted(case_ids), 2) if pair not in together and not set(pair) & blocked]
    assert not missing, f"pairs never reviewed together: {missing[:5]}"
    assert sum(part["source"] for part in plan["parts"]) == 1


@pytest.mark.parametrize("seed", range(24))
def test_random_documents_cover_every_pair_once_owned_and_within_budget(seed: int) -> None:
    rng = random.Random(seed)
    count = rng.randint(1, 200)
    sizes = [rng.choice([rng.randint(50, 1500), rng.randint(1500, 6000)]) for _ in range(count)]
    budget = rng.randint(40_000, 160_000)
    document = synthetic_document(sizes, seed=seed)
    snapshot = synthetic_snapshot(document)
    plan = _plan(snapshot, budget)
    assert not any(part["blocked_reason"] for part in plan["parts"]), "no case here is larger than a part"
    _check(plan, [case["case_id"] for case in document["test_cases"]])
    assert _plan(synthetic_snapshot(synthetic_document(sizes, seed=seed)), budget) == plan, "the plan is deterministic"


def test_a_case_larger_than_a_part_is_an_explicit_blocked_part() -> None:
    sizes = [400] * 12
    sizes[5] = 120_000
    document = synthetic_document(sizes, seed=7)
    plan = _plan(synthetic_snapshot(document), 60_000)
    big = document["test_cases"][5]["case_id"]
    blocked = [part for part in plan["parts"] if part["blocked_reason"]]
    assert blocked and all(big in part["case_ids"] for part in blocked)
    _check(plan, [case["case_id"] for case in document["test_cases"]])


def test_step5_is_one_part_and_petclinic_fits_twelve_parts_at_the_default_budget() -> None:
    step5 = build_offline_plan(step5_snapshot(), mode="compact-v1")
    assert len(step5["parts"]) == 1 and step5["parts"][0]["source"]
    _check(step5, [case["case_id"] for case in step5_snapshot()["document"]["test_cases"]])
    petclinic = build_offline_plan(petclinic_snapshot(), mode="compact-v1")
    assert len(petclinic["parts"]) <= 12 and not any(part["blocked_reason"] for part in petclinic["parts"])
    _check(petclinic, [case["case_id"] for case in petclinic_snapshot()["document"]["test_cases"]])
