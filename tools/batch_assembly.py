"""Controller-owned deterministic batch planning and candidate assembly."""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.canonical_document import json_type, validate_canonical_document_all
from tools.fixes import delete_field_fix, fix_for
from tools.schema_validation import CATEGORY_PRECEDENCE, schema_diagnostics
from tools.pilot_state import closure_cached as _closure_cached
from tools.test_design_hints import capped_code_hints, code_hints


_ROOT = Path(__file__).resolve().parents[1]
_PLAN_SCHEMA = _ROOT / "schemas" / "batch-plan.schema.json"
_FRAGMENT_SCHEMA = _ROOT / "schemas" / "candidate-fragment.schema.json"
_ASSEMBLY_SCHEMA = _ROOT / "schemas" / "assembly-receipt.schema.json"
_CONTEXT_SCHEMA = _ROOT / "schemas" / "context-selection-receipt.schema.json"


class BatchAssemblyError(ValueError):
    """A stable controller-contract failure which must not invoke review.

    ``diagnostics`` — all the rows of one check of a fragment, ``{path, code, message, fix}`` each; ``code`` is the
    controller code of the first one, ``fix`` the way out of the whole list."""

    def __init__(self, code: str, message: str, diagnostics: Sequence[Mapping[str, Any]] = (), fix: str | None = None) -> None:
        self.code = code
        self.detail = message
        if diagnostics:
            self.diagnostics = tuple(dict(row) for row in diagnostics)
        if fix:
            self.fix = fix
        elif code == "BATCH_SEMANTIC_CONFLICT" and not diagnostics:
            # PF-1: the table fix of this code sends the agent to ``diagnostics``; a refusal with none says where its reason is
            self.fix = "причина названа в message — исправь её и повтори команду; других диагностик у этого отказа нет"
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


def _schema_valid(value: dict[str, Any], schema: Path, label: str, fix: str | None = None) -> None:
    diagnostics = schema_diagnostics(value, schema, _ROOT)
    if diagnostics:
        where = f" at {diagnostics[0]['path']}" if diagnostics[0].get("path") else ""
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} schema is invalid: {diagnostics[0]['code']}{where}", fix=fix)


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


def context_digests(value: Mapping[str, Any]) -> list[str]:
    """The context receipts every batch of a plan (or a grouping) reads, in batch_index order — the list
    ``context_receipt_digests`` or, before it and for one context batch, the one ``context_receipt_digest``."""
    listed = value.get("context_receipt_digests")
    return [str(item) for item in listed] if isinstance(listed, list) and listed else [str(value.get("context_receipt_digest"))]


ANSWER_PER_TEXT_BYTE = 42  # the largest "fragment bytes / source requirement text bytes" of the 28.10 campaign (23–41)
IMPORT_ANSWER_PER_HUMAN_BYTE = 5  # a full case of the campaign is 3.9–4.5 times its human fields
NO_ROOM = "не оставляет места ответу"  # the note of a step input that leaves the answer no room; the ceiling of the context reads it
WINDOW_SHARE = 0.85  # input + expected answer of one batch stay under this share of the model window
_LETTER_ID = re.compile(r"(?:#{1,6}[^\n]*\n\s*)?(?:[-*]\s+)?([^\W\d_]{1,4})[-_.]?\d")
_HEADING = re.compile(r"#{1,2}\s+(\S.*)")


class BatchPlan(dict):
    """The plan itself (schema bytes unchanged) plus ``notes``: the lines plan-batches prints about the split."""

    notes: list[str]


def _norm(text: str) -> str:
    return " ".join(text.split())


class _SectionIndex:
    """The lines of the documents that sit under a heading, normalised once and joined by newlines (a normalised line has none), with the section of each:
    the first line that holds a snippet is the leftmost occurrence of it in the joined text.  Walking and normalising every line again for every requirement
    was n requirements x n lines (4 000 requirements in a 290 KB document = 5.5 s)."""

    def __init__(self, documents: dict[str, str]) -> None:
        lines: list[str] = []
        self.starts: list[int] = []
        self.sections: list[str] = []
        position = 0
        for path in sorted(documents):
            section = None
            for line in documents[path].splitlines():
                heading = _HEADING.match(line.strip())
                if heading:
                    section = f"{path}#{_norm(heading.group(1))}"
                elif section is not None:
                    normalised = _norm(line)
                    lines.append(normalised)
                    self.starts.append(position)
                    self.sections.append(section)
                    position += len(normalised) + 1
        self.text = "\n".join(lines)

    def section_holding(self, snippet: str) -> str | None:
        found = self.text.find(snippet)
        if found < 0:
            return None
        return self.sections[bisect.bisect_right(self.starts, found) - 1]


def _section_of(text: str, documents: dict[str, str], index: _SectionIndex | None = None) -> str | None:
    """The ``#``/``##`` section of the documents that holds the first line of *text*, or the heading *text* opens."""
    first = _norm(text.split("\n", 1)[0])
    own = _HEADING.match(first)
    if own:
        return own.group(1)
    snippet = first[:120]
    if len(snippet) < 8:
        return None
    return (index if index is not None else _SectionIndex(documents)).section_holding(snippet)


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
    index = _SectionIndex(documents)  # built once for all the requirements
    for item in source:
        if keys[item["source_requirement_id"]] is None and (section := _section_of(item["text"], documents, index)) is not None:
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


def _human_bytes(case: Mapping[str, Any]) -> int:
    return len(canonical_bytes({key: case[key] for key in ("title", "objective", "preconditions", "steps")}))


def _imported_groups(source: list[dict[str, Any]], imported: Mapping[str, Any]) -> dict[str, str | None]:
    """A case is its own group; the cases that share a source requirement and their requirements are one
    group (connected components), so a case and every requirement it traces to stay in one batch."""
    from tools.cases_import import case_sources

    parent = {item["source_requirement_id"]: item["source_requirement_id"] for item in source}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for case in imported["cases"]:
        members = [item for item in case_sources(case) if item in parent]
        for item in members[1:]:
            parent[find(item)] = find(members[0])
    return {key: "case:" + find(key) for key in parent}


def _size_split(source: list[dict[str, Any]], window_bytes: int, input_bytes: int, id_pattern: re.Pattern[str] | None,
                documents: dict[str, str], imported: Mapping[str, Any] | None = None) -> tuple[list[list[str]] | None, list[str]]:
    """Batches by the expected answer; None is one batch.  Code only splits the list of owned requirements.

    In an import the answer is the human fields of a case with its machine part (× 5), a case is its own group.
    The skeleton of a batch holds its own cases only, so the skeleton bytes of a case join its share as well."""
    if imported is not None:
        from tools.cases_import import case_sources

        share: dict[str, int] = {}
        for case in imported["cases"]:  # a case counts once, on its first source requirement
            first = case_sources(case)[0]
            share[first] = share.get(first, 0) + _human_bytes(case) * IMPORT_ANSWER_PER_HUMAN_BYTE + len(canonical_bytes(case))
        expected = {item["source_requirement_id"]: share.get(item["source_requirement_id"], 0) for item in source}
    else:
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
        return None, [f"одна пачка: вход шага {step_input} Б {NO_ROOM} в окне {window_bytes} Б — сузьте контекст"]
    if imported is not None:
        keys: dict[str, str | None] = _imported_groups(source, imported)
        counts = {"imported": len(source)}
    else:
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
        # a group over the batch budget is cut in display order, one requirement per unit — else a document of
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
    if imported is None:
        notes.extend(_end_to_end_notes(batches, keys))
    return batches, notes


def _end_to_end_notes(batches: list[list[str]], keys: dict[str, str | None]) -> list[str]:
    """A case cites only requirements of its batch, so a path through groups that sit in different batches
    cannot be one case — the plan says so instead of a hint."""
    grouped = [{keys[source] for source in members if keys.get(source)} for members in batches]
    if len(set().union(*grouped)) < 2 or any(len(groups) >= 2 for groups in grouped):
        return []
    return ["сквозной кейс через пачки невозможен: каждая пачка держит одну группу требований, а кейс ссылается только на "
            "требования своей пачки — сквозной путь строится внутри пачки"]


def _without_out_of_scope(source: list[dict[str, Any]], out_of_scope: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The source requirements the human excluded leave the plan (and so the candidate document): no batch owns them,
    the rest keep their IDs and are numbered 1..N again (the canonical document wants a contiguous display_order)."""
    ids = [str(row.get("source_requirement_id")) for row in out_of_scope]
    known = {item["source_requirement_id"] for item in source}
    unknown = sorted(set(ids) - known)
    if unknown or len(set(ids)) != len(ids) or any(not str(row.get("reason") or "").strip() for row in out_of_scope):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", f"out_of_scope names unknown, repeated or reasonless requirements: {unknown or ids}")
    kept = [item for item in source if item["source_requirement_id"] not in ids]
    if not kept:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "out_of_scope leaves no source requirement to plan")
    return [dict(item, display_order=index) for index, item in enumerate(kept, start=1)]


def in_scope_requirements(rows: Sequence[Mapping[str, Any]], plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The marker's requirements as the candidate document of that plan holds them: the excluded ones left out
    and the rest renumbered. For the checks that compare the document with the marker. Without ``out_of_scope`` the
    rows themselves."""
    excluded = {str(row.get("source_requirement_id")) for row in (plan or {}).get("out_of_scope") or [] if isinstance(row, Mapping)}
    if not excluded:
        return [dict(row) for row in rows]
    return [dict(row, display_order=index) for index, row in
            enumerate((row for row in rows if row.get("source_requirement_id") not in excluded), start=1)]


def plan_batches(source_requirements: list[dict[str, Any]], grouping_evidence: dict[str, Any], *, window_bytes: int = 0,
                 input_bytes: int = 0, id_pattern: re.Pattern[str] | None = None,
                 documents: dict[str, str] | None = None, imported: Mapping[str, Any] | None = None,
                 out_of_scope: Sequence[Mapping[str, Any]] | None = None) -> BatchPlan:
    """Create one complete partition: one batch unless the expected answer does not fit the window.

    A grouping with more than one context receipt (``context_receipt_digests``, the first one is
    ``context_receipt_digest``) gives a plan with the same list: every batch reads all of them.
    ``out_of_scope`` is ``[{source_requirement_id, reason}]`` the human excluded after the marker: no batch owns
    them, the plan names them with the reason."""
    source = _source_requirements(source_requirements)
    order = {item["source_requirement_id"]: item["display_order"] for item in source}
    if out_of_scope:
        source = _without_out_of_scope(source, out_of_scope)
    header_digest, context_digest = _evidence_digests(grouping_evidence)
    contexts = context_digests(grouping_evidence)
    if contexts[0] != context_digest or len(set(contexts)) != len(contexts):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "grouping context_receipt_digests must be unique and start with context_receipt_digest")
    groups, notes = _size_split(source, window_bytes, input_bytes, id_pattern, documents or {}, imported)
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
    body: dict[str, Any] = {"schema_version": "1.0.0", "source_requirements": source, "header_digest": header_digest, "context_receipt_digest": context_digest, "batches": batches}
    if len(contexts) > 1:  # one context batch: the plan bytes stay those of a single-context plan
        body["context_receipt_digests"] = contexts
    if out_of_scope:  # without an exclusion the plan bytes stay those of a plan with no out_of_scope
        body["out_of_scope"] = [{"source_requirement_id": str(row["source_requirement_id"]), "reason": " ".join(str(row["reason"]).split())}
                                for row in sorted(out_of_scope, key=lambda row: order[row["source_requirement_id"]])]
    plan = BatchPlan(_with_digest(body))
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan")
    plan.notes = notes
    return plan


_DOCUMENT_SUFFIXES = {".md", ".markdown", ".txt", ".adoc", ".rst"}


def plan_batches_for_project(source_requirements: list[dict[str, Any]], grouping_evidence: dict[str, Any], project: Path,
                             input_files: list[Path], context_receipt: dict[str, Any] | list[dict[str, Any]],
                             imported: Mapping[str, Any] | None = None,
                             out_of_scope: Sequence[Mapping[str, Any]] | None = None) -> BatchPlan:
    """``tools.api plan-batches``: the window and id_pattern of ``.skillsrc``, the step input as ``tools.step_size`` measures
    it (the given files, the context files of the receipts, the map of existing project tests, the skeleton of an
    import and the tc-generator role), the documents of the receipts.

    ``imported`` is the imported-cases receipt (or its value). A batch reads the skeleton of its own cases only, so
    the skeleton is no part of the common step input — the bytes of each case join the share of its batch (``_size_split``).

    ``context_receipt`` may be every context receipt of the attempt (the grouping lists them in
    ``context_receipt_digests``).  Every batch reads the receipts of the plan, so the plan takes them in ``batch_index``
    order while they add no batch (no model call more) to the plan with the first one alone; the first is always taken
    (as before), the rest the plan names in a note."""
    from tools.existing_tests import for_context, view_bytes
    from tools.step_size import measure, step_window

    project = Path(project).resolve()
    receipts = list(context_receipt) if isinstance(context_receipt, list) else [context_receipt]
    given = context_digests(grouping_evidence)
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("digest") not in given:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "context receipt does not match batch binding; fix: add "
                                     "context_receipt_digests to the grouping with every digest, the lowest batch_index first")
        _validate_context_receipt(receipt, str(receipt["digest"]))
    if sorted(str(receipt["digest"]) for receipt in receipts) != sorted(given) or len(receipts) != len(given):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "plan-batches needs every context receipt of the grouping, "
                                 "one --context-receipt each: " + ", ".join(given))
    if len(receipts) > 1:
        receipts.sort(key=lambda receipt: int(receipt["batch_index"]))
        if receipts[0]["digest"] != _evidence_digests(grouping_evidence)[1]:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "grouping context_receipt_digest must be the context receipt with the "
                                     f"lowest batch_index: {receipts[0]['digest']}")
    value = imported.get("imported", imported) if imported is not None else None
    window, pattern = step_window(project), _grouping_pattern(project)

    def plan_with(taken: list[dict[str, Any]]) -> BatchPlan:
        files: list[Path] = []
        documents: dict[str, str] = {}
        for receipt in taken:
            context_files, texts = _context_documents(project, receipt)
            files.extend(context_files)
            documents.update(texts)
        maps = len(view_bytes(for_context(project, taken)))  # one map of all the receipts the batch reads
        step_input = measure(project, [*map(Path, input_files), *files], "tc-generator", extra_bytes=maps)["bytes"]
        evidence = {key: item for key, item in grouping_evidence.items() if key != "context_receipt_digests"}
        if len(taken) > 1:
            evidence["context_receipt_digests"] = [str(receipt["digest"]) for receipt in taken]
        return plan_batches(source_requirements, evidence, window_bytes=window, input_bytes=step_input, id_pattern=pattern,
                            documents=documents, imported=value, out_of_scope=out_of_scope)

    plan, taken = plan_with(receipts[:1]), 1
    while taken < len(receipts):
        wider = plan_with(receipts[:taken + 1])
        # the context the window has room for free: no batch more (no more model calls), never «no room for the answer»
        if len(wider["batches"]) > len(plan["batches"]) or any(NO_ROOM in note for note in wider.notes):
            break
        plan, taken = wider, taken + 1
    if len(receipts) > 1:
        plan.notes.append(context_note(receipts, taken))
    if value is not None:
        plan.notes.append("вход пачки включает её часть скелета импорта: байты её кейсов входят в оценку пачки")
    # the volume of the run, so that the person sees at once how long it will take
    counted = len(plan["source_requirements"])  # the excluded requirements get no cases
    plan.notes.append(f"пачек {len(plan['batches'])}, требований {counted}"
                      + (f" (вне охвата {len(plan['out_of_scope'])})" if plan.get("out_of_scope") else "")
                      + f", ориентировочно кейсов {counted}×2–3 ({counted * 2}–{counted * 3})")
    return plan


def context_note(receipts: list[Mapping[str, Any]], taken: int) -> str:
    """Which context batches every generator batch sees — for the plan-batches output and the reviewer view."""
    seen = sum(int(receipt["byte_count"]) for receipt in receipts[:taken])
    total = sum(int(receipt["byte_count"]) for receipt in receipts)
    line = f"контекст: генератор видит {taken} из {len(receipts)} батчей контекста ({seen} из {total} Б)"
    if taken == len(receipts):
        return line
    hidden = ", ".join(f"{receipt['batch_index']} ({receipt['byte_count']} Б)" for receipt in receipts[taken:])
    return (line + f"; не видит батчи {hidden}: с ними пачек стало бы больше — по ним у генератора только сводка маркера")


def _grouping_pattern(project: Path) -> re.Pattern[str] | None:
    from tools.build_context import _project_id_pattern, compile_id_pattern

    return compile_id_pattern(_project_id_pattern(project))


def _context_documents(project: Path, context_receipt: dict[str, Any]) -> tuple[list[Path], dict[str, str]]:
    """The context files of a receipt and the text of its documents (the ``#``/``##`` sections groups come from); each path inside the
    project, checked before it is opened (tools.output_guard)."""
    from tools.output_guard import project_file

    context_files: list[Path] = []
    documents: dict[str, str] = {}
    for item in context_receipt["files"]:
        path = project_file(project, item["project_path"])
        context_files.append(path)
        if path.suffix.lower() in _DOCUMENT_SUFFIXES:
            data = path.read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != item["content_digest"]:
                raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"context file changed after selection: {item['project_path']}")
            documents[item["project_path"]] = data.decode("utf-8", errors="replace")
    return context_files, documents


def requirement_groups(source_requirements: list[dict[str, Any]], project: Path, context_receipt: dict[str, Any]) -> dict[str, str | None]:
    """The group of every source requirement exactly as plan-batches cuts batches (one rule for both): the
    ``.skillsrc`` ID pattern group, else the letter prefix, else the document section; None — no group."""
    project = Path(project).resolve()
    return _group_keys(_source_requirements(source_requirements), _grouping_pattern(project), _context_documents(project, context_receipt)[1])[0]


def _plan_batches(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "plan must be an object")
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan",
                  "--plan — сам план: файл, который команда plan записала по --out-plan (не обёртка публикации из batch-plans/ в журнале прогона)")
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
    excluded = [row.get("source_requirement_id") for row in plan.get("out_of_scope", [])]  # owned by no batch, in no document
    if len(set(excluded)) != len(excluded) or set(excluded) & set(expected):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "out_of_scope requirements must be unique and outside source_requirements")
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
        raise _refusal("BATCH_SEMANTIC_CONFLICT", "/context_receipt", "context receipt does not match batch binding",
                       "--context-receipt — квитанция этой пачки: её digest равен context_receipt_digest пачки в плане (первая по batch_index "
                       "для пачки 1); возьми ту, на которую ссылается план, и положи её же в поле context_receipt фрагмента")


def _earlier_fragments(plan: dict[str, Any], batches: dict[str, dict[str, Any]], batch: dict[str, Any],
                       fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exactly the fragments of the batches before *batch* in plan order (batches go one after another)."""
    earlier = sorted((item for item in batches.values() if item["ordinal"] < batch["ordinal"]), key=lambda item: item["ordinal"])
    by_batch = {item.get("batch_id"): item for item in fragments if isinstance(item, dict) and item.get("plan_digest") == plan["digest"]}
    missing = [item["namespace"] for item in earlier if item["batch_id"] not in by_batch]
    if missing:
        raise _refusal("BATCH_PARTITION_CONFLICT", "/prior_fragment",
                       f"batch {batch['namespace']} goes after {', '.join(missing)}: the accepted fragments of the earlier batches are missing",
                       f"пачки идут по очереди: сначала принятые ответы пачек {', '.join(missing)} (submit их стадий)")
    return [by_batch[item["batch_id"]] for item in earlier]


def _capabilities_of(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    capabilities = _unique_by_id([item for fragment in fragments for item in fragment.get("operation_capabilities") or []],
                                 "capability_id", "BATCH_ID_CONFLICT", permit_equal=True)
    return [capabilities[key] for key in sorted(capabilities)]


def prior_capabilities(plan: dict[str, Any], batch_id: str, fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """The fifth tc-generator input of batch k>=2 — the operation capabilities of the accepted batches 1..k-1."""
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
    """The same capability ID has the same bytes, the same adapter/action the same ID as in the earlier batches."""
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


def require_imported_human_fields(fragment: dict[str, Any], batch: dict[str, Any], imported: Mapping[str, Any]) -> None:
    """The cases of the batch keep the human fields of the import byte for byte — one case per imported case of
    the batch, in its order; the model completes only the machine part.  A difference names its path."""
    from tools.cases_import import canonical_management, case_sources

    owned = batch["owned_source_requirement_ids"]
    order = {source: index for index, source in enumerate(owned)}
    expected = sorted((case for case in imported["cases"] if set(case_sources(case)) & set(owned)),  # the order of slice_value
                      key=lambda case: (min(order.get(source, len(order)) for source in case_sources(case)), case["row"]))
    cases = sorted(fragment["test_cases"], key=lambda case: case.get("display_order", 0))
    differences: list[str] = []
    if len(cases) != len(expected):
        differences.append(f"/test_cases: кейсов {len(cases)}, в импорте у пачки {len(expected)}")
    for index, (case, source) in enumerate(zip(cases, expected)):
        for field in ("title", "objective", "preconditions", "priority"):
            if case.get(field) != source[field]:
                differences.append(f"/test_cases/{index}/{field}")
        if case.get("management") != canonical_management(source):  # Key, folder, labels, custom fields
            differences.append(f"/test_cases/{index}/management")
        steps = case.get("steps") or []
        if len(steps) != len(source["steps"]):
            differences.append(f"/test_cases/{index}/steps: шагов {len(steps)}, в импорте {len(source['steps'])}")
        for number, (step, row) in enumerate(zip(steps, source["steps"])):
            for field in ("action", "test_data"):
                if step.get(field) != row[field]:
                    differences.append(f"/test_cases/{index}/steps/{number}/{field}")
            if [item.get("text") for item in step.get("expectations") or []] != row["expectations"]:
                differences.append(f"/test_cases/{index}/steps/{number}/expectations")
    if differences:
        raise BatchAssemblyError("IMPORT_HUMAN_FIELD_CHANGED", "человеческие поля импорта изменены: " + ", ".join(differences[:12])
                                 + " — верни их байт в байт из входа imported, достраивай только машинную часть")
    # the case of an imported row traces to that row's source requirement only — never to another case's one
    sources: dict[str, set[str]] = {}
    for mapping in fragment.get("source_to_canonical_mappings") or []:
        for requirement_id in mapping.get("canonical_requirement_ids") or []:
            sources.setdefault(requirement_id, set()).add(mapping.get("source_requirement_id"))
    traced = [f"/test_cases/{index}/requirement_ids: ведут к {', '.join(sorted(found)) or 'ничему'}, у кейса импорта {', '.join(case_sources(source))}"
              for index, (case, source) in enumerate(zip(cases, expected))
              if (found := set().union(*(sources.get(item, set()) for item in case.get("requirement_ids") or []))) != set(case_sources(source))]
    if traced:
        raise BatchAssemblyError("IMPORT_TRACE_CHANGED", "трассировка кейсов импорта ведёт не к их строке CSV: " + "; ".join(traced[:12])
                                 + " — требования каждого кейса должны отображаться только в source requirement его строки")


def normalize_fragment(fragment: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The order the canon wants, which the code lays out before the check — ``requirement_ids`` of every case by the
    display_order of the fragment's requirements and ``categories`` by V3 precedence.  One function for the check of a fragment and
    model-response; returns a copy and the pointers it changed (``normalized``).  An id or category the rules do not know is
    left where it is for the refusal; nothing else is touched."""
    if not isinstance(fragment, dict) or fragment.get("status") != "COMPLETE":
        return fragment, []
    requirements, cases = fragment.get("requirements"), fragment.get("test_cases")
    if not isinstance(requirements, list) or not isinstance(cases, list):
        return fragment, []
    position: dict[str, int] = {}
    for index, item in enumerate(requirements):
        if isinstance(item, dict) and isinstance(item.get("requirement_id"), str):
            position.setdefault(item["requirement_id"], index)
    rank = {category: index for index, category in enumerate(CATEGORY_PRECEDENCE)}
    value = deepcopy(fragment)
    names: list[str] = []
    for index, case in enumerate(value["test_cases"]):
        if not isinstance(case, dict):
            continue
        for field, order in (("categories", rank), ("requirement_ids", position)):
            items = case.get(field)
            if isinstance(items, list) and all(isinstance(item, str) and item in order for item in items):
                ordered = sorted(items, key=order.__getitem__)
                if ordered != items:
                    case[field] = ordered
                    names.append(f"/test_cases/{index}/{field}")
    return value, names


def _missing(item: Mapping[str, Any], key: str) -> bool:
    return item.get(key) in (None, "") if key in ("step_id", "input_id", "expectation_id", "assertion_id", "display_order", "output_id") else key not in item


def fill_fragment_mechanics(fragment: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What the model need not write — the mechanics of a fragment that the code lays out by rule.

    ``display_order`` (the position, from 1) of requirements, cases, steps, inputs, outputs, expectations and assertions; the ids
    ``STEP-<tail of case_id>-<NN>``, ``INPUT-…-<NN>-<k>``, ``EXP-…-<NN>-<k>``, ``ASSERT-…-<NN>-<k>-<j>``; ``output_id`` = the source name;
    ``target.sensitive`` (true only for a secret_handle), ``manual_only`` false with ``manual_reason`` null, ``automation_blockers`` [],
    ``diagnostics`` []; and the ``semantic_type`` the code can take without a guess: of a literal (its JSON type), of a step_output (the type of the
    output), of a capability argument or result, of http_status (integer) and http_header (string).  A value the model wrote is never changed —
    a different one is the refusal the checks make (CANONICAL_DISPLAY_ORDER, SEMANTIC_DUPLICATE_ID, SEMANTIC_TYPE_MISMATCH).  Returns the copy and a
    summary ``["display_order ×N", …]`` (names and counts, not paths)."""
    if not isinstance(fragment, dict) or fragment.get("status") != "COMPLETE":
        return fragment, []
    value = deepcopy(fragment)
    counts: dict[str, int] = {}

    def put(item: dict[str, Any], key: str, new: Any, name: str | None = None) -> None:
        if _missing(item, key):
            item[key] = new
            counts[name or key] = counts.get(name or key, 0) + 1

    if "diagnostics" not in value:
        value["diagnostics"] = []
        counts["diagnostics"] = 1
    capabilities = {row["capability_id"]: row for row in value.get("operation_capabilities") or []
                    if isinstance(row, dict) and isinstance(row.get("capability_id"), str)}
    for index, requirement in enumerate(value.get("requirements") or [], start=1):
        if isinstance(requirement, dict):
            put(requirement, "display_order", index)
    for case_number, case in enumerate(value.get("test_cases") or [], start=1):
        if not isinstance(case, dict):
            continue
        put(case, "display_order", case_number)
        case_id = case.get("case_id")
        tail = case_id.removeprefix("TC-") if isinstance(case_id, str) else None
        outputs_by_step: dict[str, dict[str, Any]] = {}
        for number, step in enumerate(case.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            if tail is not None:
                put(step, "step_id", f"STEP-{tail}-{number:02d}")
            put(step, "display_order", number)
            put(step, "manual_only", False)
            if step.get("manual_only") is False:
                put(step, "manual_reason", None)
            put(step, "automation_blockers", [])
            operation = step.get("operation")
            capability = capabilities.get(operation.get("capability_id")) if isinstance(operation, dict) and operation.get("kind") == "project_action" else None
            arguments = {row.get("name"): row for row in (capability or {}).get("arguments") or [] if isinstance(row, dict)}
            results = {row.get("name"): row for row in (capability or {}).get("results") or [] if isinstance(row, dict)}
            step_id = step.get("step_id") if isinstance(step.get("step_id"), str) else None
            kept = outputs_by_step.setdefault(step_id, {}) if step_id is not None else {}
            for position, item in enumerate(step.get("inputs") or [], start=1):
                if not isinstance(item, dict):
                    continue
                if tail is not None:
                    put(item, "input_id", f"INPUT-{tail}-{number:02d}-{position}")
                put(item, "display_order", position)
                source, target = item.get("source"), item.get("target")
                if isinstance(target, dict):
                    put(target, "sensitive", isinstance(source, dict) and source.get("kind") == "secret_handle")
                if "semantic_type" not in item and isinstance(source, dict):
                    derived = None
                    if source.get("kind") == "literal" and "value" in source:
                        derived = json_type(source["value"])
                    elif source.get("kind") == "step_output":
                        derived = outputs_by_step.get(source.get("step_id"), {}).get(source.get("output_id"))
                    elif isinstance(target, dict) and target.get("location") == "arg":
                        derived = (arguments.get(target.get("name")) or {}).get("semantic_type")
                    if derived is not None:
                        item["semantic_type"] = deepcopy(derived)
                        counts["semantic_type"] = counts.get("semantic_type", 0) + 1
            for position, item in enumerate(step.get("outputs") or [], start=1):
                if not isinstance(item, dict):
                    continue
                source = item.get("source")
                put(item, "display_order", position)
                if isinstance(source, dict) and source.get("kind") in ("project_result", "http_header") and isinstance(source.get("name"), str):
                    put(item, "output_id", source["name"])
                if "semantic_type" not in item and isinstance(source, dict):
                    derived = None
                    if source.get("kind") == "project_result" and capability is not None:
                        derived = (results.get(source.get("name")) or {}).get("semantic_type")
                    elif isinstance(operation, dict) and operation.get("kind") == "http":
                        derived = {"kind": "json", "type": "integer"} if source.get("kind") == "http_status" else (
                            {"kind": "json", "type": "string"} if source.get("kind") == "http_header" else None)
                    if derived is not None:
                        item["semantic_type"] = deepcopy(derived)
                        counts["semantic_type"] = counts.get("semantic_type", 0) + 1
                if isinstance(item.get("output_id"), str) and isinstance(item.get("semantic_type"), dict):
                    kept[item["output_id"]] = item["semantic_type"]
            for e_position, expectation in enumerate(step.get("expectations") or [], start=1):
                if not isinstance(expectation, dict):
                    continue
                if tail is not None:
                    put(expectation, "expectation_id", f"EXP-{tail}-{number:02d}-{e_position}")
                put(expectation, "display_order", e_position)
                for a_position, assertion in enumerate(expectation.get("assertions") or [], start=1):
                    if isinstance(assertion, dict):
                        if tail is not None:
                            put(assertion, "assertion_id", f"ASSERT-{tail}-{number:02d}-{e_position}-{a_position}")
                        put(assertion, "display_order", a_position)
    order = ["diagnostics", "display_order", "step_id", "input_id", "output_id", "expectation_id", "assertion_id", "manual_only", "manual_reason",
             "automation_blockers", "sensitive", "semantic_type"]
    return value, [f"{name} ×{counts[name]}" for name in order if name in counts]


def fill_fragment_bindings(fragment: dict[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Audit U2: the binding fields of a fragment that the plan fixes and the answer leaves out (no key, null or "") — the
    plan, header and context digests of its batch, then the self-digest; what model-response puts there too.  A field given
    with another value stays for the refusal (its fix is «удали поле — код поставит его сам»)."""
    batch = next((row for row in plan.get("batches") or [] if isinstance(row, Mapping) and row.get("batch_id") == fragment.get("batch_id")), None)
    if batch is None:
        return fragment
    value = deepcopy(fragment)
    for key, expected in (("plan_digest", plan.get("digest")), ("header_digest", batch.get("header_digest")),
                          ("context_receipt_digest", batch.get("context_receipt_digest"))):
        if value.get(key) in (None, ""):
            value[key] = expected
    if value.get("digest") in (None, ""):
        value["digest"] = _digest({key: item for key, item in value.items() if key != "digest"})
    return value


def _refusal(code: str, path: str, message: str, fix: str) -> BatchAssemblyError:
    """PF-1: a refusal that stands alone (a binding): one row of ``diagnostics`` and its own fix, so a fix never points at rows
    the answer does not have."""
    return BatchAssemblyError(code, message, [{"path": path, "code": code, "message": message, "fix": fix}], fix)


_REQUIRED_FIXES = {
    "'semantic_type' is a required property": "укажи semantic_type: тип не выводится из значения (литерал, ссылка на выход и аргумент или результат capability его задали бы сами) — "
                                              "{\"kind\":\"json\",\"type\":\"string|integer|number|boolean|array|object|null\"}",
    "'output_id' is a required property": "укажи output_id: имя выхода, на которое ссылаются step_output; у project_result и http_header код ставит его сам как name источника, "
                                          "у http_body и http_status имя за тобой",
}


def _required_fix(row: Mapping[str, str], fragment: Mapping[str, Any]) -> str | None:
    """The fix of a missing field the code could not put by rule — what the model writes there.  An input whose type is missing because its
    step_output reference points nowhere gets the fix of that reference."""
    if row["code"] != "SCHEMA_REQUIRED":
        return None
    if row["message"] == "'semantic_type' is a required property":
        node: Any = fragment
        for token in row["path"].split("/")[1:-1]:
            node = node[int(token)] if isinstance(node, list) and token.isdigit() and int(token) < len(node) else node.get(token) if isinstance(node, dict) else None
        source = node.get("source") if isinstance(node, dict) else None
        if isinstance(source, dict) and source.get("kind") == "step_output":
            return fix_for("SEMANTIC_UNKNOWN_STEP_OUTPUT")
    return _REQUIRED_FIXES.get(row["message"])


def _row(code: str, path: str, message: str, fix: str | None = None, *, controller: str = "BATCH_SEMANTIC_CONFLICT") -> dict[str, str]:
    return {"path": path, "code": code, "message": message, "fix": fix or fix_for(code) or fix_for(controller) or ""}


def _namespace_rows(fragment: dict[str, Any]) -> list[tuple[str, str]]:
    """Every identifier of the fragment outside the namespace of its batch — (path, message)."""
    namespace = fragment["namespace"]
    prefixes = {
        "requirement_id": f"CREQ-{namespace}-", "case_id": f"TC-{namespace}-", "step_id": f"STEP-{namespace}-",
        "input_id": f"INPUT-{namespace}-", "expectation_id": f"EXP-{namespace}-", "assertion_id": f"ASSERT-{namespace}-", "blocker_id": f"BLOCK-{namespace}-",
    }
    rows: list[tuple[str, str]] = []

    def require(item: dict[str, Any], key: str, path: str) -> None:
        value = item.get(key)
        if not isinstance(value, str) or not value.startswith(prefixes[key]):
            rows.append((f"{path}/{key}", f"{key} does not use namespace {namespace}: {value!r} must start with {prefixes[key]}"))

    for r_index, requirement in enumerate(fragment["requirements"]):
        require(requirement, "requirement_id", f"/requirements/{r_index}")
    for c_index, case in enumerate(fragment["test_cases"]):
        require(case, "case_id", f"/test_cases/{c_index}")
        for s_index, step in enumerate(case["steps"]):
            base = f"/test_cases/{c_index}/steps/{s_index}"
            require(step, "step_id", base)
            for i_index, item in enumerate(step["inputs"]):
                require(item, "input_id", f"{base}/inputs/{i_index}")
            for b_index, blocker in enumerate(step["automation_blockers"]):
                require(blocker, "blocker_id", f"{base}/automation_blockers/{b_index}")
            for e_index, expectation in enumerate(step["expectations"]):
                require(expectation, "expectation_id", f"{base}/expectations/{e_index}")
                for a_index, assertion in enumerate(expectation["assertions"]):
                    require(assertion, "assertion_id", f"{base}/expectations/{e_index}/assertions/{a_index}")
    return rows


def validate_fragment(fragment: dict[str, Any], plan: dict[str, Any], header: dict[str, Any], context_receipt: dict[str, Any],
                      prior_fragments: list[dict[str, Any]] | None = None, imported: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Validate a complete or failed fragment against controller-owned bindings (and, from batch 2, the earlier batches).

    The bindings (batch_id, namespace, ownership, the header and the context receipt) are refused alone and first; every
    other violation comes in one ``BatchAssemblyError.diagnostics`` list, each row with its fix.  The order of ``requirement_ids``
    and ``categories`` is laid out by the code first — what it changed is returned as ``{"normalized": [...]}``."""
    batches = _plan_batches(plan)
    if not isinstance(fragment, dict):
        raise _refusal("BATCH_SEMANTIC_CONFLICT", "", "fragment must be an object",
                       "ответ генератора — один JSON-объект фрагмента, без конверта artifacts и без обёртки")
    fragment, normalized = normalize_fragment(fragment)
    fragment, filled = fill_fragment_mechanics(fragment)  # ids, display_order, defaults, the types the code can take
    if fragment.get("context_receipt") in (None, "") and isinstance(context_receipt, dict) and fragment.get("status") == "COMPLETE":
        fragment = {**fragment, "context_receipt": deepcopy(context_receipt)}  # the receipt of the batch given to the check (it is verified below), put by the code
        filled = [*filled, "/context_receipt"]
    fragment = fill_fragment_bindings(fragment, plan)  # audit U2: the fields model-response would put there
    batch = batches.get(fragment.get("batch_id"))
    if batch is None:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment batch_id is not in plan")
    if fragment.get("namespace") != batch["namespace"]:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment namespace does not match plan")
    if fragment.get("owned_source_requirement_ids") != batch["owned_source_requirement_ids"]:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "fragment ownership differs from plan")
    header_bytes = {key: value for key, value in header.items() if key != "digest"}
    if _digest(header_bytes) != batch["header_digest"]:
        raise _refusal("BATCH_SEMANTIC_CONFLICT", "/header_digest", "fragment/header digest binding is invalid",
                       "header — тот, что опубликован в плане (его digest без поля digest равен header_digest пачки в плане)")
    _validate_context_receipt(context_receipt, batch["context_receipt_digest"])
    if not isinstance(fragment.get("context_receipt"), dict):
        raise _refusal("BATCH_SEMANTIC_CONFLICT", "/context_receipt", "fragment lacks its context receipt",
                       "квитанцию контекста пачки ставит код при записи ответа: не пиши поле context_receipt")
    _validate_context_receipt(fragment["context_receipt"], batch["context_receipt_digest"])
    if fragment["context_receipt"] != context_receipt:
        raise _refusal("BATCH_SEMANTIC_CONFLICT", "/context_receipt", "fragment context receipt differs from verified receipt",
                       "context_receipt фрагмента должен совпасть с квитанцией пачки байт в байт: не пиши поле — код поставит его сам")
    earlier = _earlier_fragments(plan, batches, batch, prior_fragments or []) if batch["ordinal"] > 1 else []

    rows: list[tuple[str, dict[str, str]]] = []  # (controller code, row)

    def add(controller: str, code: str, path: str, message: str, fix: str | None = None) -> None:
        rows.append((controller, _row(code, path, message, fix, controller=controller)))

    def check(function: Any, *arguments: Any) -> None:
        try:
            function(*arguments)
        except BatchAssemblyError as error:
            add(error.code, error.code, "", error.detail)

    def refuse() -> None:
        first = rows[0][1]
        count = len(rows)
        raise BatchAssemblyError(
            rows[0][0],
            first["message"] if count == 1 else f"{first['message']}; всего диагностик {count} — все в diagnostics, у каждой свой fix",
            [row for _controller, row in rows],
            first["fix"] if count == 1 else f"исправь все {count} диагностик из diagnostics за один заход (у каждой свой fix) и подай фрагмент единственным повтором стадии")

    # the fields the code puts itself: another value given is the answer's mistake, the way out is to leave the field out
    for key, expected, message in (("plan_digest", plan["digest"], "fragment plan digest does not match plan"),
                                   ("header_digest", batch["header_digest"], "fragment/header digest binding is invalid"),
                                   ("context_receipt_digest", batch["context_receipt_digest"], "fragment/context digest binding is invalid")):
        if fragment.get(key) != expected:
            add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", f"/{key}", message, delete_field_fix(key))
    schema_rows = schema_diagnostics(fragment, _FRAGMENT_SCHEMA, _ROOT)
    for row in schema_rows:
        add("BATCH_SEMANTIC_CONFLICT", row["code"], row["path"], f"candidate fragment schema is invalid: {row['code']} — {row['message']}",
            _required_fix(row, fragment))
    if fragment.get("digest") != _digest({key: item for key, item in fragment.items() if key != "digest"}):
        add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", "/digest", "candidate fragment digest is invalid", delete_field_fix("digest"))
    if schema_rows:  # the shape is unreadable: the rest is judged on the corrected fragment
        refuse()
    if fragment.get("status") == "FAILED":
        if not isinstance(fragment.get("failure"), dict):
            add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", "/failure", "failed fragment lacks immutable failure evidence",
                "у фрагмента со status FAILED обязателен объект failure с причиной")
        if rows:
            refuse()
        return {"normalized": normalized} if normalized else None
    if fragment.get("status") != "COMPLETE":
        add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", "/status", "fragment status is invalid", "status — COMPLETE или FAILED")
        refuse()
    for path, message in _namespace_rows(fragment):
        add("BATCH_ID_CONFLICT", "BATCH_ID_CONFLICT", path, message)
    if batch["ordinal"] > 1:
        check(_require_prior_capabilities, fragment, earlier)
    if imported is not None:
        check(require_imported_human_fields, fragment, batch, imported)
    try:
        requirement_ids = _fragment_requirements(fragment)
    except BatchAssemblyError as error:
        add(error.code, error.code, "/requirements", error.detail)
        requirement_ids = {item.get("requirement_id") for item in fragment["requirements"] if isinstance(item, dict)}
    mappings = fragment["source_to_canonical_mappings"]
    mapping_sources = [item.get("source_requirement_id") for item in mappings if isinstance(item, dict)]
    owned = batch["owned_source_requirement_ids"]
    if sorted(mapping_sources) != sorted(owned) or len(set(mapping_sources)) != len(mapping_sources):
        add("BATCH_PARTITION_CONFLICT", "BATCH_PARTITION_CONFLICT", "/source_to_canonical_mappings",
            "mappings must cover exactly the owned source requirements: owned " + ", ".join(map(str, owned)) + "; mapped " + ", ".join(map(str, mapping_sources)))
    mapped_canonical: set[str] = set()
    for index, mapping in enumerate(mappings):
        targets = mapping.get("canonical_requirement_ids") if isinstance(mapping, dict) else None
        if not isinstance(targets, list) or not targets or any(target not in requirement_ids for target in targets):
            add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", f"/source_to_canonical_mappings/{index}/canonical_requirement_ids",
                "mapping references an unknown canonical requirement")
        else:
            mapped_canonical.update(targets)
    if mapped_canonical != requirement_ids:
        add("BATCH_SEMANTIC_CONFLICT", "BATCH_SEMANTIC_CONFLICT", "/requirements",
            "every canonical requirement must have source provenance: " + ", ".join(sorted(map(str, requirement_ids - mapped_canonical))))
    for index, case in enumerate(fragment["test_cases"]):
        references = case.get("requirement_ids") if isinstance(case, dict) else None
        if not isinstance(references, list) or not references or any(item not in requirement_ids for item in references):
            add("BATCH_PARTITION_CONFLICT", "BATCH_PARTITION_CONFLICT", f"/test_cases/{index}/requirement_ids",
                "case references a canonical requirement outside its batch")
    before = len(rows)
    check(require_batch_coverage, fragment)
    uncovered = len(rows) > before  # the same fact assemble calls SEMANTIC_UNCOVERED_REQUIREMENT: said once
    try:
        audit = _audit_rows(fragment, plan, header, earlier)
    except BatchAssemblyError as error:
        add(error.code, error.code, "", error.detail)
        audit = []
    for row in audit:
        if not (uncovered and row["code"] == "SEMANTIC_UNCOVERED_REQUIREMENT"):
            add("BATCH_SEMANTIC_CONFLICT", row["code"], row["path"],
                f"canonical pre-review audit failed: {row['code']} at {row['path']} (of this fragment) — {row['message']}")
    for path, message in _taken_groups(fragment, earlier):
        add("BATCH_SEMANTIC_CONFLICT", "SEMANTIC_PARAM_GROUP_MISMATCH", path, message,
            "дай группе этой пачки своё имя PG-…, которого нет в прошлых пачках: группа живёт в одной пачке")
    if batch["ordinal"] == max(item["ordinal"] for item in batches.values()) and all(part["status"] == "COMPLETE" for part in earlier):
        # the last batch: the rules of the whole document on the document assemble builds, so assemble refuses none of them
        try:
            document = _document(header, plan, [*earlier, fragment])
        except BatchAssemblyError as error:
            if not rows:
                add(error.code, error.code, "", error.detail)
        else:
            for row in validate_canonical_document_all(document):
                if row["code"] in DOCUMENT_WIDE_CODES:
                    add("BATCH_SEMANTIC_CONFLICT", row["code"], row["path"], f"canonical pre-review audit of the whole document failed: {row['code']} — {row['message']}")
    if rows:
        refuse()
    result = {key: names for key, names in (("normalized", normalized), ("filled", filled)) if names}
    return result or None


# the pre-review audit rules that need the whole document — they run at the last batch, on the document of all batches; every
# other rule of validate_canonical_document is decided by one batch and runs on the fragment before the answer is written. The
# param_group rules are of one batch — the cases of a group share requirement_ids and a requirement belongs to its batch; a
# name of an earlier batch is _taken_groups
DOCUMENT_WIDE_CODES = frozenset({
    "SEMANTIC_RU_RU_HUMAN_FIELDS",  # Russian prose anywhere in the document
})


def _audit_rows(fragment: dict[str, Any], plan: dict[str, Any], header: dict[str, Any], earlier: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The canonical pre-review audit of assemble on this batch alone — the document assemble would build from the
    header, the batch's own source requirements, the capabilities of the earlier batches and this one, and this fragment,
    with the same display_order — so an order or format error is refused by the check of a fragment, not first by assemble.
    All its rows (``validate_canonical_document_all``), not the first."""
    owned = set(fragment["owned_source_requirement_ids"])
    probe = {key: deepcopy(value) for key, value in header.items() if key != "digest"}
    probe.update({
        "source_requirements": [dict(deepcopy(row), display_order=index) for index, row in
                                enumerate((row for row in plan["source_requirements"] if row["source_requirement_id"] in owned), start=1)],
        "source_to_canonical_mappings": deepcopy(fragment["source_to_canonical_mappings"]),
        "operation_capabilities": list(_unique_by_id([item for part in [*earlier, fragment] for item in part["operation_capabilities"]],
                                                     "capability_id", "BATCH_ID_CONFLICT", permit_equal=True).values()),
        "requirements": deepcopy(fragment["requirements"]),
        "test_cases": deepcopy(fragment["test_cases"]),
    })
    for name in ("requirements", "test_cases"):
        for index, item in enumerate(probe[name], start=1):
            item["display_order"] = index
    return [row for row in validate_canonical_document_all(probe) if row["code"] not in DOCUMENT_WIDE_CODES]


def _taken_groups(fragment: dict[str, Any], earlier: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """A param_group name an earlier batch took — (path, message), once per name. In the document the cases of both
    batches are one group, and they cannot share requirement_ids: assemble refuses it as SEMANTIC_PARAM_GROUP_MISMATCH."""
    taken: dict[str, str] = {}
    for part in earlier:  # recorded answers: their shape is checked
        for case in part["test_cases"]:
            if "param_group" in case:
                taken.setdefault(case["param_group"], part["namespace"])
    rows: list[tuple[str, str]] = []
    for index, case in enumerate(fragment["test_cases"]):
        namespace = taken.pop(case.get("param_group"), None)  # a name once
        if namespace is not None:
            rows.append((f"/test_cases/{index}/param_group", f"{case['param_group']}: the group is already in batch {namespace}; a group lives in "
                                                             "one batch (its cases share requirement_ids, a requirement belongs to its batch)"))
    return rows


def require_batch_coverage(fragment: dict[str, Any]) -> None:
    """Every canonical requirement of this batch is referenced by a case — the rule assemble applies as
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


@_closure_cached
def assemble_candidate(header: dict[str, Any], plan: dict[str, Any], fragments: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble complete ordered fragments without semantic mutation."""
    batches = _plan_batches(plan)
    if len(fragments) != len(batches):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "there must be exactly one fragment per batch")
    by_batch: dict[str, dict[str, Any]] = {}
    ordinals = {key: item["ordinal"] for key, item in batches.items()}
    # the document is built from the canon the check of a fragment and the answer lay out — the order of the ids and
    # categories and the bindings the code puts itself — so the agent's file and the recorded answer give the same bytes
    fragments = [fill_fragment_bindings(fill_fragment_mechanics(normalize_fragment(item)[0])[0], plan) if isinstance(item, dict) else item for item in fragments]
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
    document = _document(header, plan, ordered)
    audit = validate_canonical_document_all(document)
    if audit:
        rows = [_row(row["code"], row["path"], row["message"]) for row in audit]
        raise BatchAssemblyError(
            "BATCH_SEMANTIC_CONFLICT", f"canonical pre-review audit failed: {rows[0]['code']}"
            + (f"; всего диагностик {len(rows)} — все в diagnostics, у каждой свой fix" if len(rows) > 1 else ""), rows,
            rows[0]["fix"] if len(rows) == 1 else f"исправь все {len(rows)} диагностик из diagnostics за один заход (у каждой свой fix), "
            "и подай последний фрагмент единственным повтором стадии: правила целого документа проверяет submit последней пачки")
    fingerprints: dict[str, list[str]] = defaultdict(list)
    for case in document["test_cases"]:
        fingerprints[_case_fingerprint(case)].append(case["case_id"])
    warnings = [{"code": "POSSIBLE_DUPLICATE", "fingerprint": fingerprint, "case_ids": sorted(case_ids)} for fingerprint, case_ids in sorted(fingerprints.items()) if len(case_ids) > 1]
    # code hints for the reviewer; none on a document without the new fields and vague expectations, so its bytes stay.
    warnings += capped_code_hints(code_hints(document))
    receipt = _with_digest({
        "schema_version": "1.0.0", "plan_digest": plan["digest"], "header_digest": plan["header_digest"],
        "fragment_digests": [{"batch_id": fragment["batch_id"], "digest": _digest(fragment)} for fragment in ordered],
        "document_digest": _digest(document), "pre_review_audit": {"schema": "PASS", "semantic": "PASS", "coverage": "PASS", "provenance": "PASS"}, "warnings": warnings,
    })
    _schema_valid(receipt, _ASSEMBLY_SCHEMA, "assembly receipt")
    _self_digest_valid(receipt, "assembly receipt")
    return document, receipt


def _document(header: dict[str, Any], plan: dict[str, Any], ordered: list[dict[str, Any]]) -> dict[str, Any]:
    """The canonical document of the complete fragments in plan order — what assemble audits and the last batch checks."""
    if header.get("revision") != 1 or header.get("parent_sha256") is not None:  # a later revision is the code's (subtraction), never a header's
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "the generator header must be revision 1 without a parent")
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
    return document