"""FaT-TD: code hints on the light test design the generator writes into its own answer — never a stop.

A requirement may carry ``design`` (input parameters, valid and invalid classes, numeric bounds); a case may carry
``technique`` and ``design_refs`` (which class or bound it checks; for DT/PAIRWISE one reference per parameter is
the combination).  The hints say what the references do not cover.  They name only IDs, never case text, and stay
silent on a document that uses none of the fields, so an old answer keeps its assembly receipt bytes.

FaT-Q adds hints on vague expectations that work on any document: a refusal seen only as «failed», a refused write
without a state check, a write confirmed only by «no error».  They read the machine part of the steps, rewrite nothing.
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
_SEVERITY = {"TD_NEGATIVE_MISSING": 0, "TD_VAGUE_EXPECTATION": 1, "TD_NEGATIVE_NO_STATE_CHECK": 2, "TD_BOUNDARY_MISSING": 3,
             "TD_BOUNDARY_UNDECLARED": 4, "TD_WEAK_SUCCESS": 5, "TD_REF_UNKNOWN": 6, "TD_PAIR_UNCOVERED": 7}
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


def capped(hints: list[dict[str, Any]], more: str = "TD_MORE") -> list[dict[str, Any]]:
    """At most HINT_LIMIT hints and HINT_BYTES of compact JSON; the rest is counted in one ``more`` entry."""
    kept: list[dict[str, Any]] = []
    reserve = len(f'{{"code":"{more}","detail":"и ещё 00000"}}'.encode("utf-8")) + 2
    for hint in hints:
        candidate = kept + [hint]
        if len(candidate) > HINT_LIMIT or len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + reserve > HINT_BYTES:
            break
        kept = candidate
    if len(kept) < len(hints):
        kept.append({"code": more, "detail": f"и ещё {len(hints) - len(kept)}"})
    return kept


# --- FaT-Q: vague expectations on any document -------------------------------------------------------

_OUTCOME_FLAG = re.compile(r"^(?:is_?)?(failed|failure|error|errored|success|succeeded|ok)$", re.IGNORECASE)
_FAILURE_FLAGS = {"failed", "failure", "error", "errored"}
_STATUS_NAMES = {"status", "statuscode", "status_code", "httpstatus", "http_status", "responsestatus", "response_status"}
_ERROR_NAME = re.compile(r"error|exception|violation|fault|reject|invalid", re.IGNORECASE)
_STATE_NAME = re.compile(r"added|delta|count|changed|created|deleted|removed|total|rows|unchanged|snapshot|before|after", re.IGNORECASE)
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_METHOD_INPUTS = {"method", "httpmethod", "http_method", "verb"}
_WRITE_WORDS = {"create", "add", "new", "update", "edit", "save", "delete", "remove", "post", "put", "patch", "insert", "register", "submit", "book", "cancel"}
_READ_WORDS = {"open", "get", "read", "view", "show", "list", "find", "search", "count", "fetch", "load", "check", "verify"}
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_EMPTY = ([], {}, "", 0, False, None)


def _actual_name(assertion: Mapping[str, Any]) -> str:
    actual = assertion.get("actual") or {}
    if actual.get("kind") == "http_status":
        return "status"
    return str(actual.get("name") or actual.get("output_id") or str(actual.get("pointer") or "").rsplit("/", 1)[-1])


def _literal(assertion: Mapping[str, Any]) -> Any:
    expected = assertion.get("expected") or {}
    return expected.get("value") if expected.get("kind") == "literal" else None


def _flag(assertion: Mapping[str, Any]) -> bool | None:
    """True when the assertion says «the step failed», False when «it succeeded», None when it is no outcome flag."""
    match = _OUTCOME_FLAG.match(_actual_name(assertion))
    value = _literal(assertion)
    if not match or assertion.get("operator") != "equals" or not isinstance(value, bool):
        return None
    return value == (match.group(1).lower() in _FAILURE_FLAGS)


def _is_status(assertion: Mapping[str, Any]) -> bool:
    return _actual_name(assertion).lower() in _STATUS_NAMES


def _error_status(assertion: Mapping[str, Any]) -> bool:
    value = _literal(assertion)
    return (_is_status(assertion) and assertion.get("operator") == "equals"
            and isinstance(value, int) and not isinstance(value, bool) and value >= 400)


def _status_range(assertion: Mapping[str, Any]) -> bool:
    value = _literal(assertion)
    return (_is_status(assertion) and assertion.get("operator") in {"greater_than", "greater_or_equal"}
            and isinstance(value, int) and not isinstance(value, bool) and value >= 399)


def _error_field(assertion: Mapping[str, Any]) -> bool:
    if not _ERROR_NAME.search(_actual_name(assertion)) or _flag(assertion) is not None:
        return False
    return assertion.get("operator") == "exists" or (assertion.get("operator") in {"equals", "contains", "matches", "length_equals"}
                                                     and _literal(assertion) not in _EMPTY)


def _assertions(step: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [assertion for expectation in step.get("expectations") or [] for assertion in expectation.get("assertions") or []]


def _writes(step: Mapping[str, Any], actions: Mapping[str, str]) -> bool:
    """A step changes data: an HTTP write method, a ``method`` input with one, else a write word in the capability action."""
    operation = step.get("operation") or {}
    if operation.get("kind") == "http":
        return str(operation.get("method", "")).upper() in _WRITE_METHODS
    for item in step.get("inputs") or []:
        source = item.get("source") or {}
        if str((item.get("target") or {}).get("name", "")).lower() in _METHOD_INPUTS and source.get("kind") == "literal":
            return str(source.get("value", "")).upper() in _WRITE_METHODS
    capability = str(operation.get("capability_id") or "")
    words = [word.lower() for word in _WORD.findall(actions.get(capability) or re.sub(r"^CAP-[A-Za-z0-9]+-", "", capability))]
    return bool(words) and words[0] not in _READ_WORDS and any(word in _WRITE_WORDS for word in words)


def _rejected(step: Mapping[str, Any]) -> bool:
    return any(_flag(assertion) is True or _error_status(assertion) or _status_range(assertion) or _error_field(assertion)
               for assertion in _assertions(step))


def _generic(assertion: Mapping[str, Any]) -> bool:
    """Says only «it worked»: an outcome flag, a status, existence, or a number above zero."""
    value = _literal(assertion)
    return (_flag(assertion) is not None or _is_status(assertion) or assertion.get("operator") in {"exists", "not_exists"}
            or (assertion.get("operator") in {"greater_than", "greater_or_equal"} and isinstance(value, (int, float)) and value <= 0))


def _state_checked(step: Mapping[str, Any]) -> bool:
    return any((assertion.get("expected") or {}).get("kind") == "step_output"
               or (_STATE_NAME.search(_actual_name(assertion)) and not _ERROR_NAME.search(_actual_name(assertion))
                   and _flag(assertion) is None and not _is_status(assertion))
               for assertion in _assertions(step))


def _automated(step: Mapping[str, Any]) -> bool:
    return not step.get("manual_only") and bool(step.get("operation"))


def _shown(assertion: Mapping[str, Any]) -> str:
    value = _literal(assertion)
    if _status_range(assertion):
        return f"{_actual_name(assertion)}{'>=' if assertion.get('operator') == 'greater_or_equal' else '>'}{value}"
    return f"{_actual_name(assertion)}={json.dumps(value, ensure_ascii=False)}"


def _case_hint(code: str, case: Mapping[str, Any], step: Mapping[str, Any], detail: str) -> dict[str, str]:
    return {"code": code, "case_id": case["case_id"], "step_id": step["step_id"], "detail": _clip(detail)}


def expectation_hints(document: Mapping[str, Any]) -> list[dict[str, str]]:
    """FaT-Q: a refusal without a concrete observation, a refused write without a state check, a write confirmed only by «no error».

    Reads only the machine part of the steps and names IDs and result names, never case text; silent on manual steps.
    """
    actions = {str(item.get("capability_id")): str(item.get("action") or "") for item in document.get("operation_capabilities") or []}
    hints: list[dict[str, str]] = []
    for case in document.get("test_cases") or []:
        steps = [step for step in case.get("steps") or [] if _automated(step)]
        for index, step in enumerate(steps):
            assertions = _assertions(step)
            vague = [assertion for assertion in assertions if _flag(assertion) is True or _status_range(assertion)]
            concrete = any(_error_status(assertion) or (_error_field(assertion) and assertion.get("operator") != "exists")
                           or (assertion.get("actual") or {}).get("kind") == "http_body" for assertion in assertions)
            if vague and not concrete:
                hints.append(_case_hint("TD_VAGUE_EXPECTATION", case, step, "только " + ", ".join(_shown(assertion) for assertion in vague)))
            if _writes(step, actions) and _rejected(step) and not _state_checked(step) and not any(
                    not _writes(later, actions) and _assertions(later) for later in steps[index + 1:]):
                hints.append(_case_hint("TD_NEGATIVE_NO_STATE_CHECK", case, step, "после отказа нет сверки сохранённых данных"))
        successes = [index for index, step in enumerate(steps) if _writes(step, actions) and not _rejected(step) and _assertions(step)]
        if successes:
            index = successes[-1]
            step = steps[index]
            if all(_generic(assertion) for assertion in _assertions(step)) and not any(
                    not _writes(later, actions) and any(not _generic(assertion) for assertion in _assertions(later)) for later in steps[index + 1:]):
                names = sorted({_actual_name(assertion) for assertion in _assertions(step)})
                hints.append(_case_hint("TD_WEAK_SUCCESS", case, step, "только " + ", ".join(names)))
    return hints


def code_hints(document: Mapping[str, Any]) -> list[dict[str, str]]:
    """The FaT-TD and FaT-Q hints in one order of importance (a stable sort keeps requirement and case order)."""
    return sorted(design_hints(document) + expectation_hints(document), key=lambda hint: _SEVERITY[hint["code"]])


def technique_counts(document: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter(case.get("technique", "none") for case in document.get("test_cases", []))
    return {name: counts.get(name, 0) for name in (*TECHNIQUES, "none")}
