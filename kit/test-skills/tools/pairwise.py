"""Deterministic covering arrays (D1b): the full product of the factors when it is small, otherwise a strength-2
(pairwise) array built by greedy IPOG — every pair of values of any two factors appears in some row.

No randomness and no external dependency: the same factors in the same order give the same rows.  Callers sort the
factors and their values (by design ID) before calling, so the rows do not depend on the order of their input.
"""
from __future__ import annotations

import itertools
import math
from typing import Sequence


def covering_rows(factors: Sequence[Sequence[str]], *, full_max: int) -> list[tuple[str, ...]]:
    """Rows over ``factors`` (each a list of values): the full product if it has at most ``full_max`` rows, else a
    pairwise covering array.  A row has one value per factor, in factor order."""
    factors = [list(values) for values in factors]
    if not factors or any(not values for values in factors):
        return []
    if len(factors) <= 2 or math.prod(len(values) for values in factors) <= full_max:
        return list(itertools.product(*factors))
    # IPOG on the factors by descending size (ties by position), mapped back to the caller's order at the end.
    order = sorted(range(len(factors)), key=lambda index: (-len(factors[index]), index))
    ordered = [factors[index] for index in order]
    rows: list[list[str | None]] = [list(row) for row in itertools.product(ordered[0], ordered[1])]
    for k in range(2, len(ordered)):
        missing = {(i, a, b) for i in range(k) for a in ordered[i] for b in ordered[k]}
        # Horizontal growth: each existing row takes the value of the new factor that covers most missing pairs.
        for row in rows:
            best = max(ordered[k], key=lambda value: (sum((i, row[i], value) in missing for i in range(k)), -ordered[k].index(value)))
            row.append(best)
            missing -= {(i, row[i], best) for i in range(k)}
        # Vertical growth: every pair still missing goes into a row that does not fix that factor yet, or a new row.
        added: list[list[str | None]] = []
        for i, a, b in sorted(missing, key=lambda item: (item[0], ordered[item[0]].index(item[1]), ordered[k].index(item[2]))):
            target = next((row for row in added if row[k] == b and row[i] is None), None)
            if target is None:
                target = [None] * (k + 1)
                target[k] = b
                added.append(target)
            target[i] = a
        rows.extend(added)
    # A free position keeps the factor's first value.
    filled = [tuple(value if value is not None else ordered[index][0] for index, value in enumerate(row)) for row in rows]
    back = sorted(range(len(order)), key=order.__getitem__)
    result: list[tuple[str, ...]] = []
    for row in filled:
        mapped = tuple(row[back[index]] for index in range(len(factors)))
        if mapped not in result:
            result.append(mapped)
    return result
