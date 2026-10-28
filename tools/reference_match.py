"""FaT-REF: new cases against the reference cases of a Zephyr CSV (``scan --reference``) — a hint, never a verdict.

``reference_match(document, reference_cases)`` pairs new and reference cases one to one by the Jaccard similarity of
the word tokens of title, objective and step actions, at least ``JACCARD_MIN``.  A reference case left without a pair is
a gap, a new case below the threshold is new, a new case whose best reference already has a pair is a possible
duplicate of it.  The result names cases by ID (case_id; the Key of a reference case, else its CSV row) and counts only — no
case text — and changes nothing: the reference never enters the generator input or the canon.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

CODE = "CV_REFERENCE"
# Chosen on the campaign (FaT-REF): the v5 CSV of one effective document as the reference for another.  The same cases
# (spring-petclinic 08a6e2fc → 97b27403, petclinic-rest, jsoup, jpetstore run pairs) score 1.0; the best match of a case
# against another project of the same domain stays at most 0.30 (petclinic → microservices 0.30, → petclinic-rest
# 0.26) and against an unrelated one 0.19 (commons-lang → jsoup).  0.35 sits above that noise with room for a reworded
# case of the same check.
JACCARD_MIN = 0.35
_TOKEN = re.compile(r"[0-9a-zа-яё]+")


def _tokens(parts: Iterable[str]) -> frozenset[str]:
    return frozenset(token for part in parts for token in _TOKEN.findall(str(part).lower()) if len(token) >= 3)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


def reference_label(case: Mapping[str, Any]) -> str:
    """The ID of a reference case in a hint: its Zephyr Key, else its CSV row."""
    return str(case.get("key") or f"строка {case.get('row')}")


def reference_match(document: Mapping[str, Any], reference_cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pairs one to one, the most similar first (ties in document and CSV order), so variants of one check with the same
    text pair with their own reference rows.  A new case whose best reference is taken by an equal or closer case is a
    possible duplicate of it; below ``JACCARD_MIN`` it is new; a reference row left over is a gap."""
    references = [(reference_label(case), _tokens([case.get("title", ""), case.get("objective", ""),
                                                    *(step.get("action", "") for step in case.get("steps") or [])]))
                  for case in reference_cases]
    cases = [(case["case_id"], _tokens([case.get("title", ""), case.get("objective", ""), *(step.get("action", "") for step in case["steps"])]))
             for case in document["test_cases"]]
    scores = sorted(((score, -left, -right) for left, (_case_id, tokens) in enumerate(cases)
                     for right, (_label, reference) in enumerate(references)
                     if (score := _jaccard(tokens, reference)) >= JACCARD_MIN), reverse=True)
    taken_case: dict[int, tuple[int, float]] = {}
    taken_reference: set[int] = set()
    for score, left, right in scores:
        if -left not in taken_case and -right not in taken_reference:
            taken_case[-left] = (-right, score)
            taken_reference.add(-right)
    pairs = [{"case_id": cases[index][0], "reference": references[right][0], "score": round(score, 2)}
             for index, (right, score) in sorted(taken_case.items())]
    duplicates: list[str] = []
    new: list[str] = []
    for index, (case_id, tokens) in enumerate(cases):
        if index in taken_case:
            continue
        best = max(((score, -right) for right, (_label, reference) in enumerate(references)
                    if (score := _jaccard(tokens, reference)) >= JACCARD_MIN), default=None)
        if best is None:
            new.append(case_id)
        elif references[-best[1]][0] not in duplicates:
            duplicates.append(references[-best[1]][0])
    return {"code": CODE, "reference": len(references), "cases": len(cases), "pairs": pairs,
            "gaps": [label for index, (label, _tokens_) in enumerate(references) if index not in taken_reference],
            "new": new, "duplicates": duplicates}
