"""D1b step 7: the deterministic covering arrays of ``tools.pairwise`` (no external dependency)."""
from __future__ import annotations

import itertools

from tools import pairwise


def _pairs(factors: list[list[str]]) -> set[tuple[int, str, int, str]]:
    return {(i, a, j, b) for i, j in itertools.combinations(range(len(factors)), 2) for a in factors[i] for b in factors[j]}


def _covered(factors: list[list[str]], rows: list[tuple[str, ...]]) -> set[tuple[int, str, int, str]]:
    return {(i, row[i], j, row[j]) for row in rows for i, j in itertools.combinations(range(len(factors)), 2)}


def test_a_small_product_is_generated_in_full_in_order() -> None:
    factors = [["a1", "a2"], ["b1", "b2", "b3"]]
    assert pairwise.covering_rows(factors, full_max=16) == list(itertools.product(*factors))


def test_a_product_over_the_threshold_is_a_pairwise_array() -> None:
    factors = [[f"{name}{index}" for index in range(1, 4)] for name in "abc"]
    rows = pairwise.covering_rows(factors, full_max=16)
    assert _covered(factors, rows) >= _pairs(factors)
    assert 9 <= len(rows) <= 10 < 27
    assert all(len(row) == 3 and all(value in factor for value, factor in zip(row, factors)) for row in rows)


def test_every_pair_of_mixed_sizes_is_covered_with_fewer_rows_than_the_product() -> None:
    factors = [["a1", "a2"], ["b1", "b2", "b3"], ["c1", "c2", "c3", "c4"], ["d1", "d2"], ["e1", "e2", "e3"]]
    rows = pairwise.covering_rows(factors, full_max=16)
    assert _covered(factors, rows) >= _pairs(factors)
    assert len(rows) < 2 * 3 * 4 * 2 * 3
    assert len(set(rows)) == len(rows)


def test_the_array_is_the_same_on_every_call() -> None:
    factors = [["a1", "a2", "a3"], ["b1", "b2"], ["c1", "c2", "c3"], ["d1", "d2", "d3"]]
    assert pairwise.covering_rows(factors, full_max=4) == pairwise.covering_rows([list(factor) for factor in factors], full_max=4)


def test_a_single_factor_lists_its_values_and_the_threshold_one_still_covers_every_pair() -> None:
    assert pairwise.covering_rows([["a1", "a2"]], full_max=16) == [("a1",), ("a2",)]
    factors = [["a1", "a2"], ["b1", "b2"], ["c1", "c2"]]
    rows = pairwise.covering_rows(factors, full_max=1)
    assert _covered(factors, rows) >= _pairs(factors) and len(rows) <= 6
