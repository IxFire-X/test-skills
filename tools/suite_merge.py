"""Deterministic parts of ``suite-update-v1`` (wave 3, U): the update brief, the merge of the
generator's answer into the next revision of the suite's canonical document, and the splice of
updated or repaired test methods into the package's own test files.

The model only returns what changed (cases, the canonical requirements of changed source
requirements, their mappings, retired cases, methods).  Everything else — unchanged cases,
requirements and mappings, the new ``SREQ`` numbering of the current scan (matched by key),
display order, revision and parent digest — comes from the suite and the scan.  The merged
document passes the same canonical validator and grounding check as a fresh run.

Expectations do not move silently: an updated method must contain every assertion ID of its
case and every literal expected value (``literal_diagnostics``), and a repaired method must also
keep every literal of the method it replaces (``repair_diagnostics``).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from tools.code_slices import SliceError, slice_file

_SREQ = re.compile(r"\bSREQ-\d{4,}\b")
_ID = re.compile(r"^(?P<kind>TC|CREQ)-(?P<namespace>[A-Za-z0-9]+)-(?P<number>\d{3,})$")


class MergeError(ValueError):
    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]]) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostics = [dict(row) for row in diagnostics]


def _diag(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def remap_ids(value: Any, mapping: Mapping[str, str]) -> Any:
    """Every ``SREQ-NNNN`` inside strings replaced by its new number (unknown ones stay)."""
    if isinstance(value, str):
        return _SREQ.sub(lambda match: mapping.get(match.group(0), match.group(0)), value)
    if isinstance(value, list):
        return [remap_ids(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: remap_ids(item, mapping) for key, item in value.items()}
    return value


def next_ids(document: Mapping[str, Any], namespace: str) -> dict[str, str]:
    """The first free case and canonical requirement number of a namespace."""
    def top(kind: str, items: Iterable[str]) -> int:
        numbers = [int(match.group("number")) for item in items if (match := _ID.match(item)) and match.group("kind") == kind and match.group("namespace") == namespace]
        return max(numbers, default=0)

    cases = top("TC", (case["case_id"] for case in document.get("test_cases") or []))
    creqs = top("CREQ", (row["requirement_id"] for row in document.get("requirements") or []))
    return {"case_id": f"TC-{namespace}-{cases + 1:03d}", "requirement_id": f"CREQ-{namespace}-{creqs + 1:03d}"}


def _namespace(document: Mapping[str, Any], case_ids: Sequence[str]) -> str:
    for case_id in list(case_ids) + [case["case_id"] for case in document.get("test_cases") or []]:
        match = _ID.match(case_id)
        if match:
            return match.group("namespace")
    return "U1"


def key_maps(manifest: Mapping[str, Any], scan: Sequence[Mapping[str, Any]], renamed: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """``old SREQ → new SREQ`` for every requirement that is still there (renamed ones too), and both key indexes."""
    old_sreq = {row["key"]: row["source_requirement_id"] for row in manifest["requirements"]}
    new_sreq = {row["key"]: row["source_requirement_id"] for row in scan}
    forward = {row["from"]: row["to"] for row in renamed}
    remap = {}
    for key, sreq in old_sreq.items():
        target = forward.get(key, key)
        if target in new_sreq:
            remap[sreq] = new_sreq[target]
    return remap, old_sreq, new_sreq


def update_brief(*, document: Mapping[str, Any], manifest: Mapping[str, Any], scan: Sequence[Mapping[str, Any]], impact: Mapping[str, Any],
                 edited_cases: Iterable[str] = (), proposals: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """What the generator gets in ``update`` mode; None-valued sections are omitted."""
    requirements = impact["requirements"]
    remap, old_sreq, new_sreq = key_maps(manifest, scan, requirements["renamed"])
    reverse = {row["to"]: row["from"] for row in requirements["renamed"]}
    old_text = {row["source_requirement_id"]: row["text"] for row in document.get("source_requirements") or []}
    new_text = {row["source_requirement_id"]: row["text"] for row in scan}
    creqs_of: dict[str, list[str]] = {row["source_requirement_id"]: list(row["canonical_requirement_ids"]) for row in document.get("source_to_canonical_mappings") or []}
    cases_of: dict[str, list[str]] = {}
    for case in document.get("test_cases") or []:
        for creq in case.get("requirement_ids") or []:
            cases_of.setdefault(creq, []).append(case["case_id"])
    edited = set(edited_cases)

    def linked(sreq: str) -> tuple[list[str], list[str]]:
        creqs = creqs_of.get(sreq, [])
        return creqs, sorted({case for creq in creqs for case in cases_of.get(creq, [])})

    changed = []
    for key in requirements["changed"]:
        old_key = reverse.get(key, key)
        creqs, cases = linked(old_sreq[old_key])
        changed.append({"key": key, "old_source_requirement_id": old_sreq[old_key], "new_source_requirement_id": new_sreq[key],
                        "old_text": old_text.get(old_sreq[old_key], ""), "new_text": new_text[new_sreq[key]], "canonical_requirement_ids": creqs, "case_ids": cases})
    removed = []
    for key in requirements["removed"]:
        creqs, cases = linked(old_sreq[key])
        removed.append({"key": key, "old_source_requirement_id": old_sreq[key], "old_text": old_text.get(old_sreq[key], ""), "canonical_requirement_ids": creqs, "case_ids": cases})
    added = [{"key": key, "new_source_requirement_id": new_sreq[key], "text": new_text[new_sreq[key]]} for key in requirements["added"]]
    affected = sorted((set(impact["cases"]["to_update"]) | set(impact["cases"]["to_retire"])) - edited)
    by_id = {case["case_id"]: case for case in document.get("test_cases") or []}
    involved = sorted({creq for row in changed + removed for creq in row["canonical_requirement_ids"]})
    namespace = _namespace(document, affected)
    return {
        "mode": "update", "document_id": document.get("document_id"), "revision": document.get("revision"),
        "changed_requirements": changed, "added_requirements": added, "removed_requirements": removed,
        "affected_cases": [copy.deepcopy(by_id[case_id]) for case_id in affected if case_id in by_id],
        "edited_by_people": sorted(edited & (set(impact["cases"]["to_update"]) | set(impact["cases"]["to_retire"]))),
        "canonical_requirements": [copy.deepcopy(row) for row in document.get("requirements") or [] if row["requirement_id"] in involved],
        "source_requirement_renumbering": dict(sorted(remap.items())),
        "test_gap_proposals": [dict(row) for row in proposals if row.get("case_id") in affected],
        "id_prefixes": {"namespace": namespace, **next_ids(document, namespace),
                        "step_id": f"STEP-{namespace}-", "input_id": f"INPUT-{namespace}-", "expectation_id": f"EXP-{namespace}-",
                        "assertion_id": f"ASSERT-{namespace}-", "blocker_id": f"BLOCK-{namespace}-"},
        "filled_by_driver": ["source_requirements", "unchanged cases, requirements and mappings", "SREQ numbering", "display_order", "revision",
                             "parent_sha256"],
    }


def merge_update(*, document: Mapping[str, Any], manifest: Mapping[str, Any], envelope: Mapping[str, Any], scan: Sequence[Mapping[str, Any]],
                 impact: Mapping[str, Any], answer: Mapping[str, Any], edited_cases: Iterable[str] = ()) -> tuple[dict[str, Any], dict[str, Any]]:
    """The next revision of the suite's document and what changed; ``MergeError`` with diagnostics otherwise."""
    from tools.build_context import grounding_diagnostics
    from tools.canonical_document import document_sha256, validate_canonical_document

    diagnostics: list[dict[str, str]] = []
    allowed = {"requirements", "source_to_canonical_mappings", "test_cases", "retire", "operation_capabilities", "diagnostics"}
    if not isinstance(answer, Mapping) or set(answer) - allowed or "test_cases" not in answer:
        raise MergeError("UPDATE_OUTPUT_FIELDS", [_diag("", "UPDATE_OUTPUT_FIELDS", "Return test_cases and optionally requirements, source_to_canonical_mappings, retire, operation_capabilities, diagnostics.")])
    requirements = impact["requirements"]
    remap, old_sreq, new_sreq = key_maps(manifest, scan, requirements["renamed"])
    edited = set(edited_cases)
    old_cases = {case["case_id"]: case for case in document.get("test_cases") or []}
    affected = (set(impact["cases"]["to_update"]) | set(impact["cases"]["to_retire"])) - edited
    namespace = _namespace(document, sorted(affected))
    first_new = next_ids(document, namespace)

    def is_new(identifier: str, kind: str) -> bool:
        match = _ID.match(identifier)
        start = _ID.match(first_new["case_id" if kind == "TC" else "requirement_id"])
        return bool(match and match.group("kind") == kind and match.group("namespace") == namespace and int(match.group("number")) >= int(start.group("number")))

    # Canonical requirements: the old ones, replaced or added by the answer.
    creqs = {row["requirement_id"]: copy.deepcopy(row) for row in document.get("requirements") or []}
    order = [row["requirement_id"] for row in document.get("requirements") or []]
    changed_creqs = []
    for index, row in enumerate(answer.get("requirements") or []):
        identifier = row.get("requirement_id") if isinstance(row, Mapping) else None
        if not isinstance(identifier, str) or (identifier not in creqs and not is_new(identifier, "CREQ")):
            diagnostics.append(_diag(f"/requirements/{index}", "UPDATE_REQUIREMENT_ID", f"Keep an existing canonical requirement ID or use {first_new['requirement_id']} and later."))
            continue
        if identifier not in creqs:
            order.append(identifier)
        creqs[identifier] = copy.deepcopy(dict(row))
        changed_creqs.append(identifier)
    # Mappings over the new scan: changed and added source requirements from the answer, the rest from the suite.
    answered = {row.get("source_requirement_id"): row for row in answer.get("source_to_canonical_mappings") or [] if isinstance(row, Mapping)}
    key_of_new = {row["source_requirement_id"]: row["key"] for row in scan}
    old_mappings = {row["source_requirement_id"]: row for row in document.get("source_to_canonical_mappings") or []}
    inverse = {new: old for old, new in remap.items()}
    mappings = []
    for row in envelope["artifacts"]["analytics_documentation"]["requirements"]:
        sreq = row["source_requirement_id"]
        key = key_of_new.get(sreq)
        if sreq in answered:
            mappings.append({"source_requirement_id": sreq, "canonical_requirement_ids": list(answered[sreq].get("canonical_requirement_ids") or [])})
        elif sreq in inverse and inverse[sreq] in old_mappings:
            mappings.append({"source_requirement_id": sreq, "canonical_requirement_ids": list(old_mappings[inverse[sreq]]["canonical_requirement_ids"])})
        else:
            diagnostics.append(_diag("/source_to_canonical_mappings", "UPDATE_MAPPING_MISSING", f"Map the new source requirement {sreq} ({key}) to canonical requirements."))
    for sreq in answered:
        if sreq not in key_of_new:
            diagnostics.append(_diag("/source_to_canonical_mappings", "UPDATE_MAPPING_UNKNOWN", f"{sreq} is not a source requirement of the current scan."))
    mapped = {creq for row in mappings for creq in row["canonical_requirement_ids"]}
    dropped_creqs = sorted(identifier for identifier in creqs if identifier not in mapped)
    # Cases: the old ones, replaced, added and retired by the answer.
    cases = {case_id: copy.deepcopy(case) for case_id, case in old_cases.items()}
    case_order = [case["case_id"] for case in document.get("test_cases") or []]
    changed_cases, new_cases = [], []
    for index, case in enumerate(answer.get("test_cases") or []):
        identifier = case.get("case_id") if isinstance(case, Mapping) else None
        if not isinstance(identifier, str):
            diagnostics.append(_diag(f"/test_cases/{index}", "UPDATE_CASE_ID", "Every case carries its case_id."))
            continue
        if identifier in edited:
            diagnostics.append(_diag(f"/test_cases/{index}", "UPDATE_CASE_EDITED_BY_PEOPLE", f"{identifier} was edited by a person: leave it unchanged."))
            continue
        if identifier in old_cases:
            if identifier not in affected:
                diagnostics.append(_diag(f"/test_cases/{index}", "UPDATE_CASE_NOT_AFFECTED", f"{identifier} is not affected by the requirement changes: leave it unchanged."))
                continue
            changed_cases.append(identifier)
        elif is_new(identifier, "TC"):
            case_order.append(identifier)
            new_cases.append(identifier)
        else:
            diagnostics.append(_diag(f"/test_cases/{index}", "UPDATE_CASE_ID", f"A new case uses {first_new['case_id']} and later."))
            continue
        cases[identifier] = copy.deepcopy(dict(case))
    retired = []
    for index, row in enumerate(answer.get("retire") or []):
        identifier = row.get("case_id") if isinstance(row, Mapping) else None
        if identifier not in old_cases or identifier not in affected:
            diagnostics.append(_diag(f"/retire/{index}", "UPDATE_RETIRE_FOREIGN", "Retire only an affected case of the suite."))
            continue
        retired.append({"case_id": identifier, "reason": str(row.get("reason") or "")[:500]})
        cases.pop(identifier, None)
    # Capabilities: the old ones and any new ones of the answer (an identical one is merged).
    capabilities = {row["capability_id"]: copy.deepcopy(row) for row in document.get("operation_capabilities") or []}
    for index, row in enumerate(answer.get("operation_capabilities") or []):
        identifier = row.get("capability_id") if isinstance(row, Mapping) else None
        if not isinstance(identifier, str) or identifier in capabilities and capabilities[identifier] != row:
            diagnostics.append(_diag(f"/operation_capabilities/{index}", "UPDATE_CAPABILITY_CONFLICT", "Reuse an existing capability unchanged or add a new capability_id."))
            continue
        capabilities[identifier] = copy.deepcopy(dict(row))
    if diagnostics:
        raise MergeError("UPDATE_OUTPUT_INVALID", diagnostics)
    kept_creqs = [identifier for identifier in order if identifier in creqs and identifier not in dropped_creqs]
    merged = {
        **{key: copy.deepcopy(value) for key, value in document.items() if key not in {"requirements", "source_requirements", "source_to_canonical_mappings",
                                                                                       "test_cases", "operation_capabilities"}},
        "revision": int(document.get("revision") or 1) + 1, "parent_sha256": document_sha256(dict(document)),
        "source_requirements": copy.deepcopy(envelope["artifacts"]["analytics_documentation"]["requirements"]),
        "requirements": [dict(remap_ids(creqs[identifier], remap), display_order=position) for position, identifier in enumerate(kept_creqs, start=1)],
        "source_to_canonical_mappings": mappings,
        "operation_capabilities": [remap_ids(capabilities[identifier], remap) for identifier in sorted(capabilities)],
        "test_cases": [dict(cases[identifier], display_order=position) for position, identifier in enumerate([item for item in case_order if item in cases], start=1)],
    }
    for case in merged["test_cases"]:
        case["requirement_ids"] = [identifier for identifier in kept_creqs if identifier in set(case.get("requirement_ids") or [])]
    problems = list(validate_canonical_document(merged)) + list(grounding_diagnostics(envelope, merged))
    if problems:
        raise MergeError("UPDATE_DOCUMENT_INVALID", problems)
    report = {"changed_cases": sorted(changed_cases), "new_cases": sorted(new_cases), "retired_cases": retired,
              "changed_requirements": sorted(set(changed_creqs)), "dropped_requirements": dropped_creqs,
              "edited_by_people_kept": sorted(edited & (set(impact["cases"]["to_update"]) | set(impact["cases"]["to_retire"])))}
    return merged, report


# ----------------------------------------------------------------------------------- tests

_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _literal_texts(value: Any) -> list[str]:
    """The spellings an expected literal has in source: a string as itself, a number or bool as text."""
    if isinstance(value, bool):
        return ["true" if value else "false", "True" if value else "False"]
    if isinstance(value, (int, float)):
        return [json.dumps(value)]
    if isinstance(value, str):
        return [value]
    return []


def literal_diagnostics(case: Mapping[str, Any], method_source: str, support_source: str = "") -> list[dict[str, str]]:
    """Every assertion ID of the case appears in the method and every scalar literal it expects in the method
    or in the file's SUPPORT code (a shared constant such as ``APPLICATION_JSON``) — R2 by code."""
    rows = []
    searched = method_source + "\n" + support_source
    unescaped = searched.replace('\\"', '"').replace("\\\\", "\\")
    for step in case.get("steps") or []:
        for expectation in step.get("expectations") or []:
            for assertion in expectation.get("assertions") or []:
                if assertion["assertion_id"] not in method_source:
                    rows.append(_diag(assertion["assertion_id"], "AUTOMATION_ASSERTION_MISSING", f"The method has no check labelled {assertion['assertion_id']}."))
                expected = assertion.get("expected") or {}
                if expected.get("kind") == "literal":
                    spellings = _literal_texts(expected.get("value"))
                    if spellings and not any(text in searched or text in unescaped for text in spellings):
                        rows.append(_diag(assertion["assertion_id"], "AUTOMATION_LITERAL_MISSING", f"The expected value {json.dumps(expected.get('value'), ensure_ascii=False)} is not in the method."))
    return rows


def repair_diagnostics(case: Mapping[str, Any], old_source: str, new_source: str, support_source: str = "") -> list[dict[str, str]]:
    """A repair keeps the case's checks and every string literal of the old method's assertions."""
    rows = literal_diagnostics(case, new_source, support_source)
    old = set(_STRING.findall("\n".join(line for line in old_source.splitlines() if "isEqualTo" in line or "assert" in line.lower())))
    new = set(_STRING.findall(new_source))
    for literal in sorted(old - new):
        rows.append(_diag("method", "REPAIR_EXPECTATION_CHANGED", f"The repaired method lost the literal \"{literal}\"; a repair never changes expectations."))
    return rows


def splice(path: str, content: str, *, replace: Mapping[str, str] | None = None, add: Sequence[str] = (), remove: Sequence[Mapping[str, Any]] = (),
           locators: Mapping[str, Mapping[str, Any]] | None = None, imports: Sequence[str] = ()) -> str:
    """Replace methods by locator (``replace``: locator text → new source), append new ones and helpers before
    the last closing brace (Java) or at the end (Python), remove retired ones, add missing import lines."""
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.replace("\r\n", "\n").split("\n")
    trailing = content.endswith("\n")
    if trailing:
        lines = lines[:-1]
    language = "python" if path.endswith(".py") else "java"
    targets = dict(locators or {})
    symbols = [{"symbol_id": f"S{index}", "locator": targets[name]} for index, name in enumerate(sorted(set(replace or {}) | {row["name"] for row in remove}))]
    names = sorted(set(replace or {}) | {row["name"] for row in remove})
    try:
        slices = slice_file({"file_id": "splice", "path": path, "content": "\n".join(lines) + "\n", "language": language}, symbols) if symbols else None
    except SliceError as error:
        raise MergeError("SPLICE_LOCATOR", [_diag(path, "SPLICE_LOCATOR", str(error))]) from error
    edits = []
    for index, name in enumerate(names):
        member = slices.symbols[f"S{index}"]
        source = (replace or {}).get(name)
        edits.append((member.start, member.end, [] if source is None else source.replace("\r\n", "\n").rstrip("\n").split("\n")))
    for start, end, new in sorted(edits, reverse=True):
        lines[start - 1:end] = new
    if add:
        block = []
        for source in add:
            block += [""] + source.replace("\r\n", "\n").rstrip("\n").split("\n")
        if language == "java":
            closing = max(index for index, line in enumerate(lines) if line.strip() == "}")
            lines[closing:closing] = block
        else:
            lines += block
    for line in imports:
        if line.strip() and line.strip() not in {item.strip() for item in lines}:
            anchor = max((index for index, item in enumerate(lines) if item.startswith(("import ", "from "))), default=0 if language == "python" else
                         next((index for index, item in enumerate(lines) if item.startswith("package ")), -1))
            lines.insert(anchor + 1, line.strip())
    return newline.join(lines) + (newline if trailing else "")
