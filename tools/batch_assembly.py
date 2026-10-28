"""Controller-owned deterministic batch planning and candidate assembly."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from tools.canonical_document import validate_canonical_document
from tools.schema_validation import loads_json_strict, schema_diagnostics
from tools.pilot_state import closure_cached as _closure_cached
from tools.test_design_hints import capped_code_hints, code_hints


_ROOT = Path(__file__).resolve().parents[1]
_PLAN_SCHEMA = _ROOT / "schemas" / "batch-plan.schema.json"
_FRAGMENT_SCHEMA = _ROOT / "schemas" / "candidate-fragment.schema.json"
_ASSEMBLY_SCHEMA = _ROOT / "schemas" / "assembly-receipt.schema.json"
_CONTEXT_SCHEMA = _ROOT / "schemas" / "context-selection-receipt.schema.json"


class BatchAssemblyError(ValueError):
    """A stable controller-contract failure which must not invoke review."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def with_digest(value: dict[str, Any]) -> dict[str, Any]:
    """Copy of *value* with its own canonical self-digest (batch plan, generator header)."""
    result = deepcopy(value)
    result["digest"] = _digest(result)
    return result


_with_digest = with_digest


def _schema_valid(value: dict[str, Any], schema: Path, label: str) -> None:
    diagnostics = schema_diagnostics(value, schema, _ROOT)
    if diagnostics:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} schema is invalid: {diagnostics[0]['code']}")


def _self_digest_valid(value: dict[str, Any], label: str) -> None:
    if value.get("digest") != _digest({key: item for key, item in value.items() if key != "digest"}):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} digest is invalid")


def _digest_value(value: Any, field: str = "digest") -> str:
    if isinstance(value, dict) and isinstance(value.get(field), str):
        return value[field]
    raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"missing {field}")


def _source_requirements(source_requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(source_requirements, list) or not source_requirements:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "source requirements must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in source_requirements:
        source_id = item.get("source_requirement_id") if isinstance(item, dict) else None
        if not isinstance(source_id, str) or not source_id.startswith("SREQ-") or source_id in by_id:
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "source requirement IDs must be unique SREQ IDs")
        display_order = item.get("display_order")
        if type(display_order) is not int or display_order < 1 or not isinstance(item.get("text"), str) or not item["text"].strip() or not isinstance(item.get("provenance"), list) or not item["provenance"] or not all(isinstance(value, str) and value.strip() for value in item["provenance"]) or not isinstance(item.get("digest"), str):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"source requirement {source_id} lacks exact provenance/digest")
        by_id[source_id] = deepcopy(item)
    physical = sorted(by_id.values(), key=lambda item: item["display_order"])
    if [item["display_order"] for item in physical] != list(range(1, len(physical) + 1)):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "source requirement display_order must be contiguous 1..N")
    return physical


def _evidence_digests(grouping_evidence: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(grouping_evidence, dict):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "grouping evidence must be an object")
    return _digest_value(grouping_evidence, "header_digest"), _digest_value(grouping_evidence, "context_receipt_digest")


ANSWER_PER_TEXT_BYTE = 42  # FaT-BO: the largest "fragment bytes / source requirement text bytes" of the 28.10 campaign (23–41)
WINDOW_SHARE = 0.85  # FaT-BO: input + expected answer of one batch stay under this share of the model window
_LETTER_ID = re.compile(r"(?:#{1,6}[^\n]*\n\s*)?(?:[-*]\s+)?([^\W\d_]{1,4})[-_.]?\d")
_HEADING = re.compile(r"#{1,2}\s+(\S.*)")


class BatchPlan(dict):
    """The plan itself (schema bytes unchanged) plus ``notes``: the lines plan-batches prints about the split."""

    notes: list[str]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _section_of(text: str, documents: dict[str, str]) -> str | None:
    """The ``#``/``##`` section of the documents that holds the first line of *text*, or the heading *text* opens."""
    first = _norm(text.split("\n", 1)[0])
    own = _HEADING.match(first)
    if own:
        return own.group(1)
    snippet = first[:120]
    if len(snippet) < 8:
        return None
    for path in sorted(documents):
        section = None
        for line in documents[path].splitlines():
            heading = _HEADING.match(line.strip())
            if heading:
                section = f"{path}#{_norm(heading.group(1))}"
            elif section is not None and snippet in _norm(line):
                return section
    return None


def _group_keys(source: list[dict[str, Any]], id_pattern: re.Pattern[str] | None,
                documents: dict[str, str]) -> tuple[dict[str, str | None], dict[str, int]]:
    """Group of every source requirement: ``id_pattern`` group, else letter prefix of the ID, else document section."""
    keys: dict[str, str | None] = {item["source_requirement_id"]: None for item in source}
    counts = {"id_pattern": 0, "id_prefix": 0, "section": 0}
    if id_pattern is not None and "group" in id_pattern.groupindex:
        line_id = re.compile(r"^(?:[-*]\s+)?(?:" + id_pattern.pattern + ")", re.M | (id_pattern.flags & re.I))
        for item in source:
            match = line_id.search(item["text"])
            if match and match.group("group"):
                keys[item["source_requirement_id"]] = "id:" + match.group("group")
                counts["id_pattern"] += 1
    letters = {item["source_requirement_id"]: match.group(1).upper() for item in source
               if keys[item["source_requirement_id"]] is None and (match := _LETTER_ID.match(item["text"].lstrip()))}
    if len(set(letters.values())) >= 2:  # one prefix for every ID (REQ-1, REQ-2…) is not a grouping
        for source_id, letter in letters.items():
            keys[source_id] = "prefix:" + letter
            counts["id_prefix"] += 1
    for item in source:
        if keys[item["source_requirement_id"]] is None and (section := _section_of(item["text"], documents)) is not None:
            keys[item["source_requirement_id"]] = "section:" + section
            counts["section"] += 1
    return keys, counts


def _balanced(groups: list[tuple[list[str], int]], cap: int) -> list[list[str]]:
    """Ordered groups into the fewest contiguous batches of at most *cap* expected bytes, the largest one smallest."""
    count = -(-sum(size for _members, size in groups) // cap)

    def pack(limit: int) -> list[list[str]]:
        batches: list[list[str]] = []
        load = 0
        for members, size in groups:
            if batches and load + size <= limit:
                batches[-1].extend(members)
                load += size
            else:
                batches.append(list(members))
                load = size
        return batches

    low, high = max(size for _members, size in groups), cap
    while low < high:
        middle = (low + high) // 2
        if len(pack(middle)) <= count:
            high = middle
        else:
            low = middle + 1
    return pack(low)


def _size_split(source: list[dict[str, Any]], window_bytes: int, input_bytes: int, id_pattern: re.Pattern[str] | None,
                documents: dict[str, str]) -> tuple[list[list[str]] | None, list[str]]:
    """FaT-BO: batches by the expected answer; None is one batch.  Code only splits the list of owned requirements."""
    expected = {item["source_requirement_id"]: len(item["text"].encode("utf-8")) * ANSWER_PER_TEXT_BYTE for item in source}
    total = sum(expected.values())
    if window_bytes <= 0:
        return None, ["одна пачка: деление выключено (.skillsrc limits.step_window_bytes: 0)"]
    step_input = input_bytes + len(canonical_bytes(source))
    cap = int(window_bytes * WINDOW_SHARE) - step_input
    budget = f"порог пачки {cap} Б = окно {window_bytes} Б × {WINDOW_SHARE} − вход шага {step_input} Б"
    if total <= cap:
        return None, [f"одна пачка: оценка ответа {total} Б ≤ {budget}"]
    if cap <= 0:
        return None, [f"одна пачка: вход шага {step_input} Б не оставляет места ответу в окне {window_bytes} Б — сузьте контекст"]
    keys, counts = _group_keys(source, id_pattern, documents)
    groups: dict[str, tuple[int, list[str], int]] = {}
    for item in source:
        source_id = item["source_requirement_id"]
        key = keys[source_id] or "own:" + source_id
        order, members, size = groups.get(key, (item["display_order"], [], 0))
        groups[key] = (order, members + [source_id], size + expected[source_id])
    notes: list[str] = []
    batches: list[list[str]] = []
    segment: list[tuple[list[str], int]] = []
    for _order, members, size in sorted(groups.values(), key=lambda value: value[0]):
        if size <= cap:
            segment.append((members, size))
            continue
        if segment:
            batches.extend(_balanced(segment, cap))
            segment = []
        if len(members) == 1:
            batches.append(list(members))
            notes.append(f"BATCH_GROUP_OVER_BUDGET: требование {members[0]} — оценка ответа {size} Б больше порога пачки {cap} Б; "
                         "оно не делится и идёт отдельной пачкой")
            continue
        # FaT-BO: a group over the batch budget is cut in display order, one requirement per unit — else a document of
        # FR-1…FR-40 is one prefix group and the split gives nothing
        parts: list[list[str]] = []
        units: list[tuple[list[str], int]] = []
        for member in members + [""]:
            if member and expected[member] <= cap:
                units.append(([member], expected[member]))
                continue
            if units:
                parts.extend(_balanced(units, cap))
                units = []
            if member:
                parts.append([member])
        batches.extend(parts)
        notes.append(f"BATCH_GROUP_OVER_BUDGET: группа {', '.join(members)} — оценка ответа {size} Б больше порога пачки {cap} Б; "
                     f"разрезана подряд по требованиям на {len(parts)} пачек: " + " | ".join(", ".join(part) for part in parts))
    if segment:
        batches.extend(_balanced(segment, cap))
    used = ", ".join(f"{name} {number}" for name, number in counts.items() if number)
    notes.insert(0, f"пачек {len(batches)}: оценка ответа {total} Б > {budget}; требования по группам: {used or 'нет'}")
    unplaced = [item["source_requirement_id"] for item in source if keys[item["source_requirement_id"]] is None]
    if unplaced:
        subject = "требование {} не попало" if len(unplaced) == 1 else "требования {} не попали"
        notes.append(subject.format(", ".join(unplaced)) + " ни в одну группу: нет ID группы (.skillsrc requirements.id_pattern "
                     "с группой (?P<group>…) или буквенный префикс) и первая строка не найдена в разделе # / ## документа "
                     "контекста — каждое идёт своей группой")
    return batches, notes


def plan_batches(source_requirements: list[dict[str, Any]], grouping_evidence: dict[str, Any], *, window_bytes: int = 0,
                 input_bytes: int = 0, id_pattern: re.Pattern[str] | None = None,
                 documents: dict[str, str] | None = None) -> BatchPlan:
    """Create one complete partition: one batch unless the expected answer does not fit the window (FaT-BO)."""
    source = _source_requirements(source_requirements)
    header_digest, context_digest = _evidence_digests(grouping_evidence)
    groups, notes = _size_split(source, window_bytes, input_bytes, id_pattern, documents or {})
    physical_order = {item["source_requirement_id"]: item["display_order"] for item in source}
    batches = []
    for ordinal, members in enumerate(groups or [list(physical_order)], start=1):
        members = sorted(members, key=physical_order.__getitem__)
        batch_seed = {"ordinal": ordinal, "owned_source_requirement_ids": members, "header_digest": header_digest, "context_receipt_digest": context_digest}
        batches.append({
            "batch_id": "BATCH-" + hashlib.sha256(canonical_bytes(batch_seed)).hexdigest()[:16],
            "ordinal": ordinal,
            "namespace": f"B{ordinal}",
            "owned_source_requirement_ids": members,
            "header_digest": header_digest,
            "context_receipt_digest": context_digest,
            "completeness": "COMPLETE",
        })
    plan = BatchPlan(_with_digest({"schema_version": "1.0.0", "source_requirements": source, "header_digest": header_digest, "context_receipt_digest": context_digest, "batches": batches}))
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan")
    plan.notes = notes
    return plan


_DOCUMENT_SUFFIXES = {".md", ".markdown", ".txt", ".adoc", ".rst"}


def plan_batches_for_project(source_requirements: list[dict[str, Any]], grouping_evidence: dict[str, Any], project: Path,
                             input_files: list[Path], context_receipt: dict[str, Any]) -> BatchPlan:
    """``tools.api plan-batches``: the window and id_pattern of ``.skillsrc``, the step input as ``tools.step_size`` measures
    it (the given files, the context files of the receipt and the tc-generator role), the documents of the receipt."""
    from tools.build_context import _project_id_pattern, compile_id_pattern
    from tools.step_size import measure, step_window

    project = Path(project).resolve()
    _validate_context_receipt(context_receipt, _evidence_digests(grouping_evidence)[1])
    context_files: list[Path] = []
    documents: dict[str, str] = {}
    for item in context_receipt["files"]:
        path = project / item["project_path"]
        context_files.append(path)
        if path.suffix.lower() in _DOCUMENT_SUFFIXES:
            data = path.read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != item["content_digest"]:
                raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"context file changed after selection: {item['project_path']}")
            documents[item["project_path"]] = data.decode("utf-8", errors="replace")
    step_input = measure(project, [*map(Path, input_files), *context_files], "tc-generator")["bytes"]
    return plan_batches(source_requirements, grouping_evidence, window_bytes=step_window(project), input_bytes=step_input,
                        id_pattern=compile_id_pattern(_project_id_pattern(project)), documents=documents)


def _plan_batches(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "plan must be an object")
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan")
    _self_digest_valid(plan, "plan")
    batches = plan.get("batches")
    if not isinstance(batches, list) or not batches:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "plan has no batches")
    by_id = {item.get("batch_id"): item for item in batches if isinstance(item, dict)}
    if len(by_id) != len(batches) or None in by_id:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "batch IDs must be unique")
    owned = [source_id for batch in batches for source_id in batch.get("owned_source_requirement_ids", [])]
    expected = [item.get("source_requirement_id") for item in plan.get("source_requirements", [])]
    if sorted(owned) != sorted(expected) or len(set(owned)) != len(owned):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "plan ownership is not an exact partition")
    return by_id


def _fragment_requirements(fragment: dict[str, Any]) -> set[str]:
    items = fragment.get("requirements")
    if not isinstance(items, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment requirements must be a list")
    ids = [item.get("requirement_id") for item in items if isinstance(item, dict)]
    if len(ids) != len(items) or any(not isinstance(item, str) or not item.startswith("CREQ-") for item in ids) or len(set(ids)) != len(ids):
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "canonical requirement IDs must be unique CREQ IDs")
    return set(ids)


def _validate_context_receipt(receipt: dict[str, Any], expected_digest: str) -> None:
    if not isinstance(receipt, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "context receipt must be an object")
    _schema_valid(receipt, _CONTEXT_SCHEMA, "context receipt")
    _self_digest_valid(receipt, "context receipt")
    if receipt["digest"] != expected_digest:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "context receipt does not match batch binding")


def _require_namespaced_identifiers(fragment: dict[str, Any]) -> None:
    namespace = fragment["namespace"]
    prefixes = {
        "requirement_id": f"CREQ-{namespace}-", "case_id": f"TC-{namespace}-", "step_id": f"STEP-{namespace}-",
        "input_id": f"INPUT-{namespace}-", "expectation_id": f"EXP-{namespace}-", "assertion_id": f"ASSERT-{namespace}-", "blocker_id": f"BLOCK-{namespace}-",
    }
    def require(item: dict[str, Any], key: str) -> None:
        value = item.get(key)
        if not isinstance(value, str) or not value.startswith(prefixes[key]):
            raise BatchAssemblyError("BATCH_ID_CONFLICT", f"{key} does not use namespace {namespace}")
    for requirement in fragment["requirements"]:
        require(requirement, "requirement_id")
    for case in fragment["test_cases"]:
        require(case, "case_id")
        for step in case["steps"]:
            require(step, "step_id")
            for item in step["inputs"]:
                require(item, "input_id")
            for blocker in step["automation_blockers"]:
                require(blocker, "blocker_id")
            for expectation in step["expectations"]:
                require(expectation, "expectation_id")
                for assertion in expectation["assertions"]:
                    require(assertion, "assertion_id")


def _earlier_fragments(plan: dict[str, Any], batches: dict[str, dict[str, Any]], batch: dict[str, Any],
                       fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FaT-BO: exactly the fragments of the batches before *batch* in plan order (batches go one after another)."""
    earlier = sorted((item for item in batches.values() if item["ordinal"] < batch["ordinal"]), key=lambda item: item["ordinal"])
    by_batch = {item.get("batch_id"): item for item in fragments if isinstance(item, dict) and item.get("plan_digest") == plan["digest"]}
    missing = [item["namespace"] for item in earlier if item["batch_id"] not in by_batch]
    if missing:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", f"batch {batch['namespace']} goes after {', '.join(missing)}: give the accepted "
                                 "fragments of the earlier batches (--prior-fragment, one per batch)")
    return [by_batch[item["batch_id"]] for item in earlier]


def _capabilities_of(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    capabilities = _unique_by_id([item for fragment in fragments for item in fragment.get("operation_capabilities") or []],
                                 "capability_id", "BATCH_ID_CONFLICT", permit_equal=True)
    return [capabilities[key] for key in sorted(capabilities)]


def prior_capabilities(plan: dict[str, Any], batch_id: str, fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """FaT-BO: the fifth tc-generator input of batch k>=2 — the operation capabilities of the accepted batches 1..k-1."""
    batches = _plan_batches(plan)
    batch = batches.get(batch_id)
    if batch is None:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", f"{batch_id} is not a batch of the plan")
    if batch["ordinal"] == 1:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "the first batch has no earlier capabilities: its input is the four digests")
    earlier = _earlier_fragments(plan, batches, batch, fragments)
    return with_digest({
        "schema_version": "1.0.0", "plan_digest": plan["digest"], "batch_id": batch_id,
        "fragment_digests": [{"batch_id": item["batch_id"], "digest": item["digest"]} for item in earlier],
        "operation_capabilities": _capabilities_of(earlier),
    })


def _require_prior_capabilities(fragment: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
    """FaT-BO: the same capability ID has the same bytes, the same adapter/action the same ID as in the earlier batches."""
    known = {item["capability_id"]: item for item in _capabilities_of(earlier)}
    actions = {(item.get("adapter"), item.get("action")): key for key, item in known.items()}
    for capability in fragment["operation_capabilities"]:
        key = capability.get("capability_id")
        owner = known.get(key) or known.get(actions.get((capability.get("adapter"), capability.get("action"))))
        if owner is not None and canonical_bytes(owner) != canonical_bytes(capability):
            raise BatchAssemblyError(
                "BATCH_CAPABILITY_CONFLICT",
                f"capability {key} differs from capability {owner['capability_id']} of an earlier batch; take it verbatim from the "
                f"prior capabilities input: {canonical_bytes(owner).decode('utf-8')}")


def validate_fragment(fragment: dict[str, Any], plan: dict[str, Any], header: dict[str, Any], context_receipt: dict[str, Any],
                      prior_fragments: list[dict[str, Any]] | None = None) -> None:
    """Validate a complete or failed fragment against controller-owned bindings (and, from batch 2, the earlier batches)."""
    batches = _plan_batches(plan)
    if not isinstance(fragment, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment must be an object")
    _schema_valid(fragment, _FRAGMENT_SCHEMA, "candidate fragment")
    _self_digest_valid(fragment, "candidate fragment")
    batch = batches.get(fragment.get("batch_id"))
    if batch is None:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment batch_id is not in plan")
    if fragment.get("namespace") != batch["namespace"]:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment namespace does not match plan")
    if fragment.get("plan_digest") != plan["digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment plan digest does not match plan")
    header_bytes = {key: value for key, value in header.items() if key != "digest"}
    if fragment.get("header_digest") != batch["header_digest"] or _digest(header_bytes) != batch["header_digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment/header digest binding is invalid")
    if fragment.get("context_receipt_digest") != batch["context_receipt_digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment/context digest binding is invalid")
    _validate_context_receipt(context_receipt, batch["context_receipt_digest"])
    _validate_context_receipt(fragment["context_receipt"], batch["context_receipt_digest"])
    if fragment["context_receipt"] != context_receipt:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment context receipt differs from verified receipt")
    if fragment.get("owned_source_requirement_ids") != batch["owned_source_requirement_ids"]:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "fragment ownership differs from plan")
    if fragment.get("status") == "FAILED":
        if not isinstance(fragment.get("failure"), dict):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failed fragment lacks immutable failure evidence")
        return
    if fragment.get("status") != "COMPLETE":
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment status is invalid")
    _require_namespaced_identifiers(fragment)
    if batch["ordinal"] > 1:
        _require_prior_capabilities(fragment, _earlier_fragments(plan, batches, batch, prior_fragments or []))
    requirement_ids = _fragment_requirements(fragment)
    mappings = fragment.get("source_to_canonical_mappings")
    if not isinstance(mappings, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment mappings must be a list")
    mapping_sources = [item.get("source_requirement_id") for item in mappings if isinstance(item, dict)]
    if sorted(mapping_sources) != sorted(batch["owned_source_requirement_ids"]) or len(set(mapping_sources)) != len(mapping_sources):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "mappings must cover exactly the owned source requirements")
    mapped_canonical: set[str] = set()
    for mapping in mappings:
        targets = mapping.get("canonical_requirement_ids") if isinstance(mapping, dict) else None
        if not isinstance(targets, list) or not targets or any(target not in requirement_ids for target in targets):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "mapping references an unknown canonical requirement")
        mapped_canonical.update(targets)
    if mapped_canonical != requirement_ids:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "every canonical requirement must have source provenance")
    cases = fragment.get("test_cases")
    if not isinstance(cases, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment cases must be a list")
    for case in cases:
        references = case.get("requirement_ids") if isinstance(case, dict) else None
        if not isinstance(references, list) or not references or any(item not in requirement_ids for item in references):
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "case references a canonical requirement outside its batch")
    require_batch_coverage(fragment)


def require_batch_coverage(fragment: dict[str, Any]) -> None:
    """FaT-DG: every canonical requirement of this batch is referenced by a case — the rule assemble applies as
    SEMANTIC_UNCOVERED_REQUIREMENT (canonical_document.uncovered_requirements), checked on the batch alone."""
    from tools.canonical_document import uncovered_requirements

    if fragment.get("status") != "COMPLETE":
        return
    requirements = [item for item in fragment.get("requirements") or [] if isinstance(item, dict)]
    cases = [item for item in fragment.get("test_cases") or [] if isinstance(item, dict)]
    missing = [requirements[index].get("requirement_id") for index in uncovered_requirements(requirements, cases)]
    if missing:
        raise BatchAssemblyError(
            "BATCH_UNCOVERED_REQUIREMENT",
            f"requirements without a test case: {', '.join(map(str, missing))}; every requirement of the batch needs a case "
            "(or a case with an explicit blocker) — this is an error of the answer, it takes the one retry of the stage",
        )


def _unique_by_id(items: list[dict[str, Any]], key: str, code: str, *, permit_equal: bool = False) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item.get(key)
        if not isinstance(item_id, str):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"missing {key}")
        existing = result.get(item_id)
        if existing is not None:
            if canonical_bytes(existing) != canonical_bytes(item):
                raise BatchAssemblyError(code, f"{key} {item_id} has different bytes")
            if permit_equal:
                continue
            raise BatchAssemblyError("BATCH_ID_CONFLICT", f"duplicate {key} {item_id}")
        result[item_id] = deepcopy(item)
    return result


def _case_fingerprint(case: dict[str, Any]) -> str:
    def without_identity(value: Any) -> Any:
        if isinstance(value, list):
            return [without_identity(item) for item in value]
        if isinstance(value, dict):
            return {key: without_identity(item) for key, item in value.items() if key != "display_order" and key != "requirement_ids" and not key.endswith("_id")}
        return value
    return _digest(without_identity(case))


@_closure_cached  # FaT-9b
def assemble_candidate(header: dict[str, Any], plan: dict[str, Any], fragments: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble complete ordered fragments without semantic mutation."""
    batches = _plan_batches(plan)
    if len(fragments) != len(batches):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "there must be exactly one fragment per batch")
    by_batch: dict[str, dict[str, Any]] = {}
    ordinals = {key: item["ordinal"] for key, item in batches.items()}
    for fragment in sorted(fragments, key=lambda item: ordinals.get(item.get("batch_id"), 0) if isinstance(item, dict) else 0):
        validate_fragment(fragment, plan, header, fragment.get("context_receipt"), list(by_batch.values()))
        batch_id = fragment["batch_id"]
        if batch_id in by_batch:
            raise BatchAssemblyError("BATCH_ID_CONFLICT", "multiple fragments for one batch")
        by_batch[batch_id] = fragment
    if set(by_batch) != set(batches):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "fragments do not cover the complete plan")
    ordered = [by_batch[item["batch_id"]] for item in sorted(batches.values(), key=lambda item: item["ordinal"])]
    if any(fragment["status"] == "FAILED" for fragment in ordered):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failed fragment cannot assemble a canonical candidate")
    capabilities = _unique_by_id([item for fragment in ordered for item in fragment["operation_capabilities"]], "capability_id", "BATCH_ID_CONFLICT", permit_equal=True)
    requirements = _unique_by_id([item for fragment in ordered for item in fragment["requirements"]], "requirement_id", "BATCH_ID_CONFLICT")
    cases = _unique_by_id([item for fragment in ordered for item in fragment["test_cases"]], "case_id", "BATCH_ID_CONFLICT")
    canonical_owners: dict[str, str] = {}
    for fragment in ordered:
        for mapping in fragment["source_to_canonical_mappings"]:
            for canonical_id in mapping["canonical_requirement_ids"]:
                owner = canonical_owners.setdefault(canonical_id, fragment["batch_id"])
                if owner != fragment["batch_id"]:
                    raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "one canonical requirement cannot span batches")
    mappings = [deepcopy(item) for fragment in ordered for item in fragment["source_to_canonical_mappings"]]
    document = {key: deepcopy(value) for key, value in header.items() if key != "digest"}
    document.update({
        "source_requirements": deepcopy(plan["source_requirements"]),
        "source_to_canonical_mappings": mappings,
        "operation_capabilities": list(capabilities.values()),
        "requirements": list(requirements.values()),
        "test_cases": list(cases.values()),
    })
    for name in ("requirements", "test_cases"):
        for index, item in enumerate(document[name], start=1):
            item["display_order"] = index
    diagnostics = validate_canonical_document(document)
    if diagnostics:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"canonical pre-review audit failed: {diagnostics[0]['code']}")
    fingerprints: dict[str, list[str]] = defaultdict(list)
    for case in document["test_cases"]:
        fingerprints[_case_fingerprint(case)].append(case["case_id"])
    warnings = [{"code": "POSSIBLE_DUPLICATE", "fingerprint": fingerprint, "case_ids": sorted(case_ids)} for fingerprint, case_ids in sorted(fingerprints.items()) if len(case_ids) > 1]
    # FaT-TD/FaT-Q: code hints for the reviewer; none on a document without the new fields and vague expectations, so its bytes stay.
    warnings += capped_code_hints(code_hints(document))
    receipt = _with_digest({
        "schema_version": "1.0.0", "plan_digest": plan["digest"], "header_digest": plan["header_digest"],
        "fragment_digests": [{"batch_id": fragment["batch_id"], "digest": _digest(fragment)} for fragment in ordered],
        "document_digest": _digest(document), "pre_review_audit": {"schema": "PASS", "semantic": "PASS", "coverage": "PASS", "provenance": "PASS"}, "warnings": warnings,
    })
    _schema_valid(receipt, _ASSEMBLY_SCHEMA, "assembly receipt")
    _self_digest_valid(receipt, "assembly receipt")
    return document, receipt


def _publish_immutable_json(root: Path, directory_name: str, batch_id: str, value: dict[str, Any], label: str) -> dict[str, Any]:
    directory = root / directory_name
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} directory is unsafe")
    target = directory / f"{batch_id}.json"
    payload = canonical_bytes(value)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as error:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", f"immutable {label} already exists") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        raw = target.read_bytes()
        readback = loads_json_strict(raw.decode("utf-8"))
        if raw != payload or readback != value:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} readback differs from published bytes")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path": str(target), "digest": value["digest"], "byte_digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "bytes": payload, "readback": readback}


def publish_fragment(attempt_artifact_dir: Path, fragment: dict[str, Any]) -> dict[str, Any]:
    """Create/read back a fragment and its failed-model receipt under a caller-owned attempt root."""
    _schema_valid(fragment, _FRAGMENT_SCHEMA, "candidate fragment")
    _self_digest_valid(fragment, "candidate fragment")
    root = Path(attempt_artifact_dir).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "attempt artifact directory is unsafe")
    model_publication = None
    if fragment["status"] == "FAILED":
        failure = fragment["failure"]
        model_receipt = failure["model_receipt"]
        _self_digest_valid(model_receipt, "model receipt")
        if failure["evidence_digest"] != model_receipt["digest"]:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failure evidence digest does not bind model receipt")
        model_publication = _publish_immutable_json(root, "model-receipts", fragment["batch_id"], model_receipt, "model receipt")
    fragment_publication = _publish_immutable_json(root, "fragments", fragment["batch_id"], fragment, "fragment")
    if model_publication is not None:
        fragment_publication["model_receipt"] = model_publication
    return fragment_publication
