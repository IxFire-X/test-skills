"""Automation generator by parts (P2): the part plan, the part brief, the check of a part answer and the merge.

Pure functions of the effective document and the part answers, so the plan and the merged answer can be recomputed
and checked.  One part means the run keeps the single automation task.

- The plan: whole cases in document order, every case exactly once, at most ``cases_per_part`` cases (0: no limit) and
  every part document (compact JSON) within the byte budget; parts are balanced by count, and their number grows until
  every part also fits by bytes.
- Every part writes its own test files: file IDs start with ``FILE-PNNN-``, file names carry ``PartNNN``; helper code
  stays inside each class (no shared support file), so parts are independent.
- The merge unites the parts in order; a path or file ID used twice is an error.
"""
from __future__ import annotations

import hashlib
import json
import math
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


def _even_split(items: Sequence[str], count: int) -> list[list[str]]:
    size, extra = divmod(len(items), count)
    groups, start = [], 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        groups.append(list(items[start:end]))
        start = end
    return groups


def plan_parts(document: Mapping[str, Any], budget: int, *, cases_per_part: int = 0) -> list[dict[str, Any]]:
    """Balanced parts in document order: at most ``cases_per_part`` cases each (0: no limit) and within ``budget`` bytes."""
    cases = [str(case["case_id"]) for case in document.get("test_cases", [])]
    if not cases:
        raise PartPlanError("AUTOMATION_PARTS_EMPTY", "the document has no test case")
    if len(set(cases)) != len(cases):
        raise PartPlanError("AUTOMATION_PARTS_INVALID", "the document repeats a case_id")
    for case_id in cases:
        if len(part_document_bytes(document, [case_id])) > budget:
            raise PartPlanError("TASK_INPUT_TOO_LARGE", f"case {case_id} alone is over the part budget of {budget} bytes")
    count = max(1, math.ceil(len(cases) / cases_per_part) if cases_per_part > 0 else 1)
    while True:
        groups = _even_split(cases, count)
        if all(len(part_document_bytes(document, group)) <= budget for group in groups):
            return [{"part_id": f"part-{index:03d}", "case_ids": group} for index, group in enumerate(groups, 1)]
        count += 1  # an even count is not an even size: one more part, split again (ends at one case per part)


def plan_receipt(document: Mapping[str, Any], plan: Sequence[Mapping[str, Any]], *, fixed_bytes: int, budget: int, cases_per_part: int,
                 single_task_input_bytes: int) -> dict[str, Any]:
    """What the split costs: the input of every part (context and brief travel with each) against the single task.

    ``single_task_input_bytes`` is what the driver measured for the one task (its document is indented JSON).
    """
    rows = []
    for part in plan:
        data = part_document_bytes(document, part["case_ids"])
        rows.append({"part_id": part["part_id"], "case_ids": list(part["case_ids"]), "document_bytes": len(data),
                     "document_digest": "sha256:" + hashlib.sha256(data).hexdigest(), "input_bytes": fixed_bytes + len(data)})
    return {"schema_version": "1.0.0", "parts": len(rows), "budget": budget, "cases_per_part": cases_per_part, "fixed_bytes": fixed_bytes,
            "rows": rows, "total_input_bytes": sum(row["input_bytes"] for row in rows), "single_task_input_bytes": int(single_task_input_bytes)}


def part_number(part_id: str) -> int:
    return int(str(part_id).rsplit("-", 1)[1])


def part_marker(part_id: str) -> str:
    return f"Part{part_number(part_id):03d}"


def file_id_prefix(part_id: str) -> str:
    return f"FILE-P{part_number(part_id):03d}-"


def part_brief(part: Mapping[str, Any], *, count: int) -> dict[str, Any]:
    number, marker, prefix = part_number(part["part_id"]), part_marker(part["part_id"]), file_id_prefix(part["part_id"])
    return {"part_id": part["part_id"], "owned_case_ids": list(part["case_ids"]), "file_id_prefix": prefix, "name_marker": marker,
            "instructions": (f"Это часть {number} из {count} автоматизации: автоматизируй только кейсы owned_case_ids (в документе части только они). "
                             f"Каждый файл — свой: file_id начинается с {prefix}, имя файла и класса содержит {marker} "
                             f"(например, ...{marker}Test.java или test_..._{marker.lower()}.py). Вспомогательный код держи внутри своего класса, "
                             "не ссылайся на файлы других частей.")}


def part_answer_problems(part_id: str, case_ids: Sequence[str], value: Mapping[str, Any]) -> list[dict[str, str]]:
    """A part answer keeps to its prefix, its name marker and its cases, and covers all of them."""
    prefix, marker, owned = file_id_prefix(part_id), part_marker(part_id), set(case_ids)
    problems: list[dict[str, str]] = []
    for index, row in enumerate(value.get("generated_files") or []):
        if not str(row.get("file_id", "")).startswith(prefix):
            problems.append({"path": f"/generated_files/{index}/file_id", "code": "AUTOMATION_PART_FILE_ID", "message": f"file_id must start with {prefix}"})
        name = str(row.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
        if marker.lower() not in name.lower():
            problems.append({"path": f"/generated_files/{index}/path", "code": "AUTOMATION_PART_PATH", "message": f"the file name must contain {marker}"})
    for index, row in enumerate(value.get("generated_symbols") or []):
        if not str(row.get("file_id", "")).startswith(prefix):
            problems.append({"path": f"/generated_symbols/{index}/file_id", "code": "AUTOMATION_PART_FILE_ID", "message": f"file_id must start with {prefix}"})
    covered: set[str] = set()
    for name in ("implementation_relations", "manual_dispositions"):
        for index, row in enumerate(value.get(name) or []):
            case_id = str(row.get("case_id"))
            covered.add(case_id)
            if case_id not in owned:
                problems.append({"path": f"/{name}/{index}/case_id", "code": "AUTOMATION_PART_CASE", "message": f"{case_id} belongs to another part"})
    missing = [case_id for case_id in case_ids if case_id not in covered]
    if missing:
        problems.append({"path": "/implementation_relations", "code": "AUTOMATION_PART_COVERAGE",
                         "message": "every case of the part needs a relation or a manual disposition: " + ", ".join(missing)})
    return problems


def merge_part_answers(answers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One automation answer from the part answers, in part order (the driver fills digests and order afterwards)."""
    if not answers:
        raise PartPlanError("AUTOMATION_PARTS_EMPTY", "nothing to merge")
    files: list[dict[str, Any]] = []
    paths: set[str] = set()
    file_ids: set[str] = set()
    for answer in answers:
        for row in answer.get("generated_files") or []:
            path = str(row.get("path", "")).replace("\\", "/").casefold()
            if path in paths or row.get("file_id") in file_ids:
                raise PartPlanError("AUTOMATION_PARTS_INVALID", f"two parts write the same file: {row.get('path')}")
            paths.add(path)
            file_ids.add(row.get("file_id"))
            files.append(dict(row))
    warnings: list[str] = []
    for answer in answers:
        for item in answer.get("warnings") or []:
            if item not in warnings:
                warnings.append(item)
    return {"automation_status": "BLOCKED" if any(answer.get("automation_status") == "BLOCKED" for answer in answers) else "GENERATED",
            "generated_files": files,
            "generated_symbols": [dict(row) for answer in answers for row in answer.get("generated_symbols") or []],
            "implementation_relations": [dict(row) for answer in answers for row in answer.get("implementation_relations") or []],
            "manual_dispositions": [dict(row) for answer in answers for row in answer.get("manual_dispositions") or []],
            "diagnostics": [dict(row) for answer in answers for row in answer.get("diagnostics") or []],
            "warnings": warnings}
