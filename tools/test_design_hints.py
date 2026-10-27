"""FaT-TD: code hints on the light test design the generator writes into its own answer — never a stop.

A requirement may carry ``design`` (input parameters, valid and invalid classes, numeric bounds); a case may carry
``technique`` and ``design_refs`` (which class or bound it checks; for DT/PAIRWISE one reference per parameter is
the combination).  The hints say what the references do not cover.  They name only IDs, never case text, and stay
silent on a document that uses none of the fields, so an old answer keeps its assembly receipt bytes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections import Counter
from typing import Any, Iterable, Mapping

HINT_LIMIT = 50
HINT_BYTES = 4096
DETAIL_LIMIT = 200  # the assembly receipt schema bound; no allowed input may exceed it
PAIRS_PER_REQUIREMENT = 3
# Important hints first, so many pairs never push a missing negative case out of the capped list.
_SEVERITY = {"TD_NEGATIVE_MISSING": 0, "TD_BOUNDARY_MISSING": 1, "TD_BOUNDARY_UNDECLARED": 2, "TD_REF_UNKNOWN": 3, "TD_PAIR_UNCOVERED": 4}
TECHNIQUES = ("EP", "BVA", "DT", "PAIRWISE", "OTHER")
_COMBINING = {"DT", "PAIRWISE"}
_BOUND_SIDES = {"min": ("min", "below_min"), "max": ("max", "above_max")}
# A leading requirement ID or section number ("3.1", "O01.", "REQ-7") is not a bound.
_LEADING_ID = re.compile(r"^\s*(?:[A-Za-zА-Яа-я]{0,8}-?\d+(?:\.\d+)*\.?|\d+(?:\.\d+)+\.?)\s+")
_NUMBER = r"\d+(?:[.,]\d+)?"
_COMPARISON = re.compile(
    rf"(?:\bот\s+{_NUMBER}\s+до\s+{_NUMBER}"
    rf"|\bне\s+(?:более|менее|больше|меньше)\s+{_NUMBER}"
    rf"|\b(?:максимум|минимум)\s+{_NUMBER}"
    rf"|\bдо\s+{_NUMBER}\s+символ"
    rf"|(?:≤|≥|<=|>=|<|>)\s*{_NUMBER})",
    re.IGNORECASE,
)


def uses_test_design(document: Mapping[str, Any]) -> bool:
    return any("design" in requirement for requirement in document.get("requirements", [])) or any(
        "technique" in case for case in document.get("test_cases", []))


def _clip(text: str) -> str:
    """Deterministic cut to DETAIL_LIMIT: the head and a short digest of the whole text."""
    if len(text) <= DETAIL_LIMIT:
        return text
    return text[: DETAIL_LIMIT - 10] + "…" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:9]


def _hint(code: str, requirement_id: str, parameter: str | None, detail: str) -> dict[str, str]:
    return {"code": code, "requirement_id": requirement_id, **({"parameter": parameter} if parameter else {}), "detail": _clip(detail)}


def has_comparison(text: str) -> bool:
    lines = [_LEADING_ID.sub("", line, count=1) for line in str(text).splitlines()]
    return any(_COMPARISON.search(line) for line in lines)


def design_hints(document: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every gap between the declared design and the cases' references, in requirement order."""
    if not uses_test_design(document):
        return []
    hints: list[dict[str, str]] = []
    cases = list(document.get("test_cases", []))
    for requirement in sorted(document.get("requirements", []), key=lambda item: item.get("display_order", 0)):
        requirement_id = requirement["requirement_id"]
        parameters = {parameter["id"]: parameter for parameter in (requirement.get("design") or {}).get("parameters", [])}
        own = [case for case in cases if requirement_id in case.get("requirement_ids", [])]
        refs = [(case, ref) for case in own for ref in case.get("design_refs", []) if ref.get("parameter") in parameters]
        for case in own:
            for ref in case.get("design_refs", []):
                parameter = parameters.get(ref.get("parameter"))
                known_elsewhere = any(ref.get("parameter") in {p["id"] for p in (other.get("design") or {}).get("parameters", [])}
                                      for other in document.get("requirements", []) if other["requirement_id"] in case.get("requirement_ids", []))
                if parameter is None:
                    if not known_elsewhere and case.get("requirement_ids", [None])[0] == requirement_id:
                        hints.append(_hint("TD_REF_UNKNOWN", requirement_id, ref.get("parameter"), f"{case['case_id']}:{ref.get('parameter')}"))
                    continue
                classes = {item["id"] for item in parameter["valid"] + parameter["invalid"]}
                bounds = parameter.get("bounds") or {}
                bad_class = "class" in ref and ref["class"] not in classes
                bad_bound = "bound" in ref and not any(ref["bound"] in sides and side in bounds for side, sides in _BOUND_SIDES.items())
                if bad_class or bad_bound:
                    hints.append(_hint("TD_REF_UNKNOWN", requirement_id, parameter["id"], f"{case['case_id']}:{ref.get('class') or ref.get('bound')}"))
        for parameter_id, parameter in parameters.items():
            covered = {ref.get("class") for _case, ref in refs if ref["parameter"] == parameter_id}
            for item in parameter["invalid"]:
                if item["id"] not in covered:
                    hints.append(_hint("TD_NEGATIVE_MISSING", requirement_id, parameter_id, item["id"]))
            bounds = parameter.get("bounds") or {}
            hit = {ref.get("bound") for _case, ref in refs if ref["parameter"] == parameter_id}
            for side, names in _BOUND_SIDES.items():
                if side in bounds:
                    for name in names:
                        if name not in hit:
                            hints.append(_hint("TD_BOUNDARY_MISSING", requirement_id, parameter_id, name))
        hints.extend(_pair_hints(requirement_id, parameters, own))
        if has_comparison(requirement.get("text", "")) and not any(parameter.get("bounds") for parameter in parameters.values()):
            hints.append(_hint("TD_BOUNDARY_UNDECLARED", requirement_id, None, "в тексте есть сравнение или диапазон, границ в design нет"))
    order = {requirement["requirement_id"]: index for index, requirement in enumerate(
        sorted(document.get("requirements", []), key=lambda item: item.get("display_order", 0)))}
    return sorted(hints, key=lambda hint: (_SEVERITY[hint["code"]], order.get(hint["requirement_id"], 0)))


def _pair_hints(requirement_id: str, parameters: Mapping[str, Mapping[str, Any]], cases: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Every pair of valid classes of two parameters must meet in some DT/PAIRWISE case (only with ≥ 3 parameters)."""
    factors = {parameter_id: [item["id"] for item in parameter["valid"]] for parameter_id, parameter in parameters.items() if parameter["valid"]}
    if len(factors) < 3:
        return []
    combinations = [{ref["parameter"]: ref.get("class") for ref in case.get("design_refs", []) if "class" in ref}
                    for case in cases if case.get("technique") in _COMBINING]
    missing = [f"{first}={a},{second}={b}"
               for (first, first_values), (second, second_values) in itertools.combinations(sorted(factors.items()), 2)
               for a, b in itertools.product(first_values, second_values)
               if not any(row.get(first) == a and row.get(second) == b for row in combinations)]
    hints = [_hint("TD_PAIR_UNCOVERED", requirement_id, None, detail) for detail in missing[:PAIRS_PER_REQUIREMENT]]
    if len(missing) > PAIRS_PER_REQUIREMENT:
        hints.append(_hint("TD_PAIR_UNCOVERED", requirement_id, None, f"и ещё {len(missing) - PAIRS_PER_REQUIREMENT} пар"))
    return hints


def capped(hints: list[dict[str, str]]) -> list[dict[str, str]]:
    """At most HINT_LIMIT hints and HINT_BYTES of compact JSON; the rest is counted in one TD_MORE entry."""
    kept: list[dict[str, str]] = []
    reserve = len('{"code":"TD_MORE","detail":"и ещё 00000"}'.encode("utf-8")) + 2
    for hint in hints:
        candidate = kept + [hint]
        if len(candidate) > HINT_LIMIT or len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + reserve > HINT_BYTES:
            break
        kept = candidate
    if len(kept) < len(hints):
        kept.append({"code": "TD_MORE", "detail": f"и ещё {len(hints) - len(kept)}"})
    return kept


def technique_counts(document: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter(case.get("technique", "none") for case in document.get("test_cases", []))
    return {name: counts.get(name, 0) for name in (*TECHNIQUES, "none")}
