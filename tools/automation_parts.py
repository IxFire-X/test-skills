"""Part plan of the automation generator (P2): whole cases in document order, every part within a byte budget.

A pure function of the effective document and the budget left for the document after the context and the brief, so
the plan can be recomputed and checked.  One part means the run keeps the single automation task.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


class PartPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def part_document(document: Mapping[str, Any], case_ids: Sequence[str]) -> dict[str, Any]:
    """The document a part reads: the whole header (requirements, mappings, capabilities) and only its cases, in order."""
    wanted = set(case_ids)
    return {**{key: value for key, value in document.items() if key != "test_cases"},
            "test_cases": [case for case in document.get("test_cases", []) if case.get("case_id") in wanted]}


def part_document_bytes(document: Mapping[str, Any], case_ids: Sequence[str]) -> bytes:
    """Compact UTF-8 JSON of the part document, as the part task carries it."""
    return json.dumps(part_document(document, case_ids), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_parts(document: Mapping[str, Any], budget: int) -> list[dict[str, Any]]:
    """Greedy, deterministic: add cases in document order while the part document stays within ``budget`` bytes."""
    cases = [str(case["case_id"]) for case in document.get("test_cases", [])]
    if not cases:
        raise PartPlanError("AUTOMATION_PARTS_EMPTY", "the document has no test case")
    header = len(part_document_bytes(document, []))
    sizes = {case_id: len(part_document_bytes(document, [case_id])) - header for case_id in cases}
    plan: list[list[str]] = []
    current: list[str] = []
    used = header
    for case_id in cases:
        if header + sizes[case_id] > budget:
            raise PartPlanError("TASK_INPUT_TOO_LARGE", f"case {case_id} alone needs {header + sizes[case_id]} bytes, over the part budget of {budget} bytes")
        # +1 for the comma between cases in the compact array.
        if current and used + sizes[case_id] + 1 > budget:
            plan.append(current)
            current, used = [], header
        used += sizes[case_id] + (1 if current else 0)
        current.append(case_id)
    plan.append(current)
    # The estimate above is exact for compact JSON; confirm it, never hand out a part over the budget.
    for case_ids in plan:
        if len(part_document_bytes(document, case_ids)) > budget:
            raise PartPlanError("AUTOMATION_PARTS_INVALID", "a planned part exceeds its budget")
    return [{"part_id": f"part-{index:03d}", "case_ids": case_ids} for index, case_ids in enumerate(plan, 1)]
