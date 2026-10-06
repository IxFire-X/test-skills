"""Deterministic bounded review inputs and aggregation; no model execution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]


def review_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def review_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(review_bytes(value)).hexdigest()


def _rows(code: str, path: str = "") -> list[dict[str, str]]:
    return [{"code": code, "path": path, "message": "Bounded review evidence is incomplete or invalid."}]


def part_input(plan: Mapping[str, Any], part: Mapping[str, Any]) -> dict[str, Any]:
    """The exact UTF-8 JSON envelope sent to this fresh invocation."""
    envelope = {"plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
                "review_kind": plan["snapshot"]["review_kind"], "revision": plan["snapshot"]["revision"],
                "part_id": part["part_id"], "instructions": plan["snapshot"]["instructions"],
                "scopes": _compact_scopes(part["scopes"])}
    # Plans frozen before the index existed keep their exact envelopes.
    if plan["snapshot"].get("document_index") is not None:
        envelope["document_index"] = copy.deepcopy(plan["snapshot"]["document_index"])
    return envelope


def document_index(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Every case of the reviewed document with its requirements.

    A part sees only its own scopes; the index lets the reviewer address a
    required check to a case or requirement outside them (``case_ids``,
    ``requirement_ids``), and the controller picks the scopes that hold them.
    """
    document = snapshot["document"]
    return {"cases": [{"case_id": case["case_id"], "title": case["title"], "requirement_ids": list(case["requirement_ids"])}
                      for case in document["test_cases"]]}


def _local_requirement_ids(scope: Mapping[str, Any]) -> set[str]:
    """Canonical and source requirement IDs a local scope carries as whole-object inputs."""
    ids: set[str] = set()
    for item in scope["inputs"]:
        for prefix, key in (("/requirements/", "requirement_id"), ("/source_requirements/", "source_requirement_id")):
            if item["pointer"].startswith(prefix) and item["pointer"].count("/") == 2:
                try:
                    value = json.loads(item["content"])
                except ValueError:
                    continue
                if isinstance(value, dict) and isinstance(value.get(key), str):
                    ids.add(value[key])
    return ids


def resolve_check(plan: Mapping[str, Any], check: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one required check to the exact base scopes it names, or None when it names unknown ones.

    A reviewer sees only its own part, so it may address evidence by ``case_ids``
    or ``requirement_ids``; the controller adds the local scopes holding them.  A
    check given by ``scope_ids`` alone normalizes to itself, so earlier check
    digests (and the scope IDs derived from them) stay the same.
    """
    base = [scope for part in plan["parts"] for scope in part["scopes"]]
    known = {scope["scope_id"] for scope in base}
    scope_ids = list(check.get("scope_ids") or [])
    if any(scope_id not in known for scope_id in scope_ids):
        return None
    local = [scope for scope in base if scope["kind"] == "local"]
    for case_id in check.get("case_ids") or []:
        scope = next((scope for scope in local if scope["scope_id"] == "local-" + case_id), None)
        if scope is None:
            return None
        if scope["scope_id"] not in scope_ids:
            scope_ids.append(scope["scope_id"])
    for requirement_id in check.get("requirement_ids") or []:
        holders = [scope for scope in local if requirement_id in _local_requirement_ids(scope)]
        if not holders:
            return None
        scope_ids.extend(scope["scope_id"] for scope in holders if scope["scope_id"] not in scope_ids)
    if not scope_ids:
        return None
    return {"scope_ids": scope_ids, "reason": check["reason"]}


def _compact_scopes(scopes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy scopes for the model, sending each distinct piece of evidence once per part.

    Scopes of one part overlap heavily (a cross check repeats both of its local
    scopes).  The first occurrence of an input keeps its ``content``; an exact
    repeat keeps its identity (digest, pointer, range) and names the first
    occurrence in ``content_ref`` instead of carrying the same bytes again.
    """
    first: dict[tuple[Any, ...], dict[str, Any]] = {}
    compact: list[dict[str, Any]] = []
    for scope in scopes:
        copied = {key: copy.deepcopy(value) for key, value in scope.items() if key != "inputs"}
        copied["inputs"] = []
        for index, item in enumerate(scope["inputs"]):
            identity = (item.get("artifact_digest"), item.get("pointer"), item.get("start"), item.get("end"), item.get("content"))
            entry = {key: copy.deepcopy(value) for key, value in item.items() if key != "content"}
            if identity in first:
                entry["content_ref"] = dict(first[identity])
            else:
                entry["content"] = item.get("content")
                first[identity] = {"scope_id": scope["scope_id"], "input": index}
            copied["inputs"].append(entry)
        # Keep the original key order of a scope (inputs before question).
        compact.append({key: copied[key] for key in scope if key in copied})
    return compact


def _part(plan: Mapping[str, Any], scopes: list, index: int, requested_check: str | None = None) -> dict:
    part = {"part_id": f"part-{index:06d}", "scopes": copy.deepcopy(scopes),
            "requested_check": requested_check, "input_byte_count": 0, "blocked_reason": None}
    part["input_byte_count"] = len(review_bytes(part_input(plan, part)))
    if part["input_byte_count"] + plan["snapshot"]["response_reserve_bytes"] > plan["input_byte_budget"]:
        part["blocked_reason"] = "REVIEW_CONTEXT_LIMIT"
    return part


def build_review_plan(snapshot: Mapping[str, Any], scopes: Sequence[Mapping[str, Any]], *, input_byte_budget: int) -> dict[str, Any]:
    """Pack already bound scopes in stable order; an oversized scope stays explicit."""
    plan = {"schema_version": "1.0.0", "snapshot": copy.deepcopy(dict(snapshot)),
            "input_byte_budget": input_byte_budget, "parts": [], "digest": "sha256:" + "0" * 64}
    if not scopes or {scope.get("kind") for scope in scopes} != {"source", "local", "cross"}:
        raise ValueError("review requires original-source, local and cross scopes")
    if len({scope.get("scope_id") for scope in scopes}) != len(scopes):
        raise ValueError("review scope ownership must be unique")
    current: list = []
    for scope in scopes:
        proposed = _part(plan, [*current, scope], len(plan["parts"]) + 1)
        if current and proposed["blocked_reason"]:
            plan["parts"].append(_part(plan, current, len(plan["parts"]) + 1))
            current = []
        current.append(scope)
    if current:
        plan["parts"].append(_part(plan, current, len(plan["parts"]) + 1))
    plan["digest"] = review_digest({key: value for key, value in plan.items() if key != "digest"})
    if validate_review_plan(plan):
        raise ValueError("invalid review plan")
    return plan


_VALID_PLAN_DIGESTS: set[str] = set()


def validate_review_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validate a plan with its ledger additions; an already proven exact value is not re-proven."""
    try:
        identity = review_digest({key: value for key, value in plan.items() if key != "unavailable"})
    except (TypeError, ValueError):
        identity = None
    if identity is not None and identity in _VALID_PLAN_DIGESTS:
        return []
    rows = _validate_review_plan(plan)
    if not rows and identity is not None:
        if len(_VALID_PLAN_DIGESTS) >= 1024:
            _VALID_PLAN_DIGESTS.clear()
        _VALID_PLAN_DIGESTS.add(identity)
    return rows


def _validate_review_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    base = {key: value for key, value in plan.items() if key not in {"additions", "unavailable"}}
    rows = schema_diagnostics(base, ROOT / "schemas/review-plan.schema.json", ROOT)
    if rows:
        return rows
    if base["digest"] != review_digest({key: value for key, value in base.items() if key != "digest"}):
        return _rows("REVIEW_PLAN_DIGEST")
    parts = [*base["parts"], *plan.get("additions", [])]
    rows = schema_diagnostics({**base, "parts": parts}, ROOT / "schemas/review-plan.schema.json", ROOT)
    if rows:
        return rows
    scopes = [scope for part in parts for scope in part["scopes"]]
    if ([part["part_id"] for part in parts] != [f"part-{index:06d}" for index in range(1, len(parts) + 1)]
            or len({scope["scope_id"] for scope in scopes}) != len(scopes)
            or {scope["kind"] for scope in scopes} != {"source", "local", "cross"}):
        rows.extend(_rows("REVIEW_PLAN_OWNERSHIP"))
    for part in parts:
        expected = _part(base, part["scopes"], int(part["part_id"].split("-")[1]), part["requested_check"])
        if part != expected:
            rows.extend(_rows("REVIEW_PART_INPUT", part["part_id"]))
    return rows


def validate_review_part(plan: Mapping[str, Any], part: Mapping[str, Any], result: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = schema_diagnostics(dict(result), ROOT / "schemas/review-part-output.schema.json", ROOT)
    if rows:
        return rows
    if (part not in [*plan["parts"], *plan.get("additions", [])]
            or part["blocked_reason"] is not None
            or result["plan_digest"] != plan["digest"]
            or result["snapshot_digest"] != plan["snapshot"]["snapshot_digest"]
            or result["part_id"] != part["part_id"]
            or result["input_digest"] != review_digest(part_input(plan, part))):
        rows.extend(_rows("REVIEW_PART_BINDING", part["part_id"]))
    if [row["scope_id"] for row in result["coverage"]] != [scope["scope_id"] for scope in part["scopes"]]:
        rows.extend(_rows("REVIEW_PART_COVERAGE", part["part_id"]))
    if any(resolve_check(plan, check) is None for check in result["required_checks"]):
        rows.extend(_rows("REVIEW_CHECK_SCOPE", part["part_id"]))
    if plan["snapshot"]["review_kind"] == "tc-reviewer" and any(
        not any(_contains_correction(scope, correction) for scope in part["scopes"])
        for correction in result["corrections"]
    ):
        rows.extend(_rows("REVIEW_CORRECTION_SCOPE", part["part_id"]))
    return rows


def _contains_correction(scope: Mapping[str, Any], correction: Mapping[str, Any]) -> bool:
    return any(ref["start"] is None and ref["end"] is None and ref["pointer"]
               and (correction["path"] == ref["pointer"] or correction["path"].startswith(ref["pointer"] + "/"))
               for ref in scope["inputs"])


def additional_review_parts(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Register flat, exact-source cross checks requested by completed parts."""
    parts = [*plan["parts"], *plan.get("additions", [])]
    known = {part["requested_check"] for part in parts}
    additions = []
    for check in _required_checks(plan, results):
        digest = review_digest(check)
        if digest in known:
            continue
        known.add(digest)
        additions.append(_check_part(plan, check, len(parts) + len(additions) + 1))
    return additions


def _required_checks(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict]:
    parts = {part["part_id"]: part for part in [*plan["parts"], *plan.get("additions", [])]}
    valid = [result for result in results if result.get("part_id") in parts and not validate_review_part(plan, parts[result["part_id"]], result)]
    checks = [resolve_check(plan, check) for result in valid for check in result["required_checks"]]
    corrections = []
    correction_sets = []
    base_ids = {part["part_id"] for part in plan["parts"]}
    for result in valid:
        if result["part_id"] not in base_ids and corrections and corrections not in correction_sets:
            correction_sets.append(list(corrections))
        for correction in result["corrections"]:
            if correction not in corrections:
                corrections.append(correction)
    if corrections and corrections not in correction_sets:
        correction_sets.append(list(corrections))
    # A correction must be considered against the same original cross evidence.
    # These are flat additions, never mutations of already reviewed inputs.
    if corrections and plan["snapshot"]["review_kind"] == "tc-reviewer":
        completed = {result["part_id"] for result in results}
        if all(part["part_id"] in completed or part["blocked_reason"] or part["part_id"] in plan.get("unavailable", {}) for part in plan["parts"]):
            for proposed in correction_sets:
                for part in plan["parts"]:
                    for scope in part["scopes"]:
                        if scope["kind"] == "cross":
                            related = [item for item in proposed if set(item["related_ids"]) & set(scope["targets"]) or any(item["path"].startswith(ref["pointer"] + "/") for ref in scope["inputs"] if ref["pointer"])]
                            if not related:
                                related = proposed
                            scope_ids = [scope["scope_id"]]
                            for correction in related:
                                original = next(item for base in plan["parts"] for item in base["scopes"] if _contains_correction(item, correction))
                                if original["scope_id"] not in scope_ids:
                                    scope_ids.append(original["scope_id"])
                            checks.append({"scope_ids": scope_ids, "reason": "Verify these exact proposed corrections preserve cross-case meaning and introduce no conflict: " + review_bytes(related).decode("utf-8")})
    return checks


def _check_part(plan: Mapping[str, Any], check: Mapping[str, Any], index: int) -> dict:
    scopes = {scope["scope_id"]: scope for part in plan["parts"] for scope in part["scopes"]}
    inputs, targets = [], []
    for scope_id in check["scope_ids"]:
        for item in scopes[scope_id]["inputs"]:
            if item not in inputs:
                inputs.append(item)
        targets.extend(target for target in scopes[scope_id]["targets"] if target not in targets)
    digest = review_digest(check)
    scope = {"scope_id": "cross-" + digest[7:], "kind": "cross", "targets": targets,
             "inputs": inputs, "question": check["reason"]}
    return _part(plan, [scope], index, digest)


def aggregate_review_parts(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]], *, resolve_unchecked: bool = True) -> dict[str, Any]:
    """Reconcile exact coverage claims; provenance is verified by pilot_state.

    ``resolve_unchecked=False`` reproduces aggregates sealed before 2026-10-06, which
    never closed an UNCHECKED scope through a later check part.
    """
    diagnostics = validate_review_plan(plan)
    parts = [*plan["parts"], *plan.get("additions", [])]
    by_id: dict[str, list] = {}
    for result in results:
        by_id.setdefault(str(result.get("part_id")), []).append(result)
    unknown = set(by_id) - {part["part_id"] for part in parts}
    if unknown:
        diagnostics.extend(_rows("REVIEW_FOREIGN_PART"))
    # Each open row: (part_id, unchecked row, digests of the checks its own answer requested).
    open_rows: list[tuple[str, dict[str, Any], list[str]]] = []
    findings, corrections, required_checks, checked, bindings = [], [], [], [], []
    for part in parts:
        candidates = by_id.get(part["part_id"], [])
        errors = validate_review_part(plan, part, candidates[0]) if len(candidates) == 1 else _rows("REVIEW_PART_MISSING_OR_DUPLICATE")
        if errors:
            diagnostics.extend(errors)
            open_rows.extend((part["part_id"], {"scope_id": scope["scope_id"], "reason": plan.get("unavailable", {}).get(part["part_id"]) or part["blocked_reason"] or errors[0]["code"]}, [])
                             for scope in part["scopes"])
            continue
        result = candidates[0]
        bindings.append({"part_id": part["part_id"], "result_digest": review_digest(result)})
        own_checks = [review_digest(resolve_check(plan, check)) for check in result["required_checks"]]
        for coverage in result["coverage"]:
            if coverage["status"] == "CHECKED":
                checked.append(coverage["scope_id"])
            else:
                open_rows.append((part["part_id"], {"scope_id": coverage["scope_id"], "reason": coverage["assessment"]}, own_checks))
        findings.extend(result["findings"])
        corrections.extend(item for item in result["corrections"] if item not in corrections)
    required_checks = _required_checks(plan, results)
    requested = {review_digest(check): check for check in required_checks}
    for index, part in enumerate(parts, start=1):
        if part["requested_check"] is not None:
            check = requested.get(part["requested_check"])
            if check is None or part != _check_part(plan, check, index):
                diagnostics.extend(_rows("REVIEW_ADDITION_BINDING", part["part_id"]))
    # A scope left UNCHECKED because its envelope lacked evidence is answered by the
    # check parts its own answer requested.  When every such part came back CHECKED,
    # the early UNCHECKED is closed and the link is kept in the aggregate.
    resolved: list[dict[str, Any]] = []
    while resolve_unchecked:
        closing = {part["requested_check"]: part["part_id"] for part in parts
                   if part["requested_check"] is not None and all(scope["scope_id"] in checked for scope in part["scopes"])}
        ready = [row for row in open_rows if row[2] and all(digest in closing for digest in row[2])]
        if not ready:
            break
        for row in ready:
            open_rows.remove(row)
            checked.append(row[1]["scope_id"])
            resolved.append({"part_id": row[0], "scope_id": row[1]["scope_id"], "resolved_by": sorted({closing[digest] for digest in row[2]})})
    unchecked = [row[1] for row in open_rows]
    closed = {part["requested_check"] for part in parts if all(scope["scope_id"] in checked for scope in part["scopes"])}
    for digest, check in requested.items():
        if digest not in closed:
            unchecked.append({"scope_id": "cross-" + digest[7:], "reason": check["reason"]})
    complete = not diagnostics and not unchecked
    blocked = bool(diagnostics or unchecked or any(item["severity"] == "BLOCKING" for item in findings))
    aggregate = {"plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
                 "addition_digests": [review_digest(part) for part in plan.get("additions", [])],
                 "parts": bindings, "checked_scope_ids": checked, "unchecked": unchecked,
                 "findings": findings, "corrections": corrections, "required_checks": required_checks,
                 "complete": complete, "blocked": blocked, "eligible": complete and not blocked,
                 "diagnostics": diagnostics}
    if resolved:
        # Present only when something was resolved: earlier aggregates recompute byte for byte.
        aggregate["resolved_unchecked"] = resolved
    return aggregate


def _pointer_value(value: Any, pointer: str) -> Any:
    for token in pointer.split("/")[1:] if pointer else []:
        key = token.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def snapshot_inputs(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Index immutable parent bytes; ranges and JSON pointers retain parent digests."""
    result = {review_digest(snapshot["document"]): snapshot["document"]}
    if snapshot["automation"] is not None:
        result[review_digest(snapshot["automation"])] = snapshot["automation"]
    for source in [*snapshot["sources"], *snapshot["contexts"]]:
        result[source["sha256"]] = source["content"]
    return result


def validate_scope_inputs(snapshot: Mapping[str, Any], scopes: Sequence[Mapping[str, Any]]) -> None:
    parents = snapshot_inputs(snapshot)
    for scope in scopes:
        for item in scope["inputs"]:
            parent = parents[item["artifact_digest"]]
            if item["start"] is None and item["end"] is None:
                content = review_bytes(_pointer_value(parent, item["pointer"])).decode("utf-8")
            elif isinstance(parent, str) and item["pointer"] == "" and type(item["start"]) is int and type(item["end"]) is int:
                raw = parent.encode("utf-8")
                if not 0 <= item["start"] < item["end"] <= len(raw):
                    raise ValueError("invalid review source range")
                content = raw[item["start"]:item["end"]].decode("utf-8")
            else:
                raise ValueError("invalid review source reference")
            if item["content"] != content:
                raise ValueError("review input differs from its immutable parent bytes")


def review_scopes(snapshot: Mapping[str, Any], *, source_chunk_bytes: int) -> list[dict[str, Any]]:
    """Structural units and bounded pair comparisons, without inferred independence."""
    document = snapshot["document"]
    automation = snapshot["automation"]

    def ref(parent, pointer):
        return {"artifact_digest": review_digest(parent), "pointer": pointer, "start": None,
                "end": None, "content": review_bytes(_pointer_value(parent, pointer)).decode("utf-8")}

    def raw_ref(source, start, end):
        return {"artifact_digest": source["sha256"], "pointer": "", "start": start, "end": end,
                "content": source["content"].encode("utf-8")[start:end].decode("utf-8")}

    scopes = []
    # Original bytes are partitioned independently of generator mappings. Every
    # byte belongs to a source check, including prose with no generated SREQ ID.
    for source_index, source in enumerate(snapshot["sources"]):
        raw = source["content"].encode("utf-8")
        start = 0
        while start < len(raw):
            end = min(start + max(1, source_chunk_bytes), len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end += 1
            inputs = [raw_ref(source, start, end)]
            source_ids = set()
            for index, requirement in enumerate(document["source_requirements"]):
                if any(source["path"] in evidence for evidence in requirement["provenance"]):
                    # Exact source text can be assigned to its original byte range.
                    # Ambiguous or rewritten text stays visible in every range.
                    text = requirement["text"].encode("utf-8")
                    position = raw.find(text)
                    if position >= 0 and raw.find(text, position + 1) < 0 and not (position < end and position + len(text) > start):
                        continue
                    inputs.append(ref(document, f"/source_requirements/{index}"))
                    source_ids.add(requirement["source_requirement_id"])
            req_ids = {rid for mapping in document["source_to_canonical_mappings"] if mapping["source_requirement_id"] in source_ids for rid in mapping["canonical_requirement_ids"]}
            for index, requirement in enumerate(document["requirements"]):
                if requirement["requirement_id"] in req_ids:
                    inputs.append(ref(document, f"/requirements/{index}"))
            scopes.append({"scope_id": f"source-{source_index:06d}-{start:012d}", "kind": "source",
                           "targets": [source["path"]], "inputs": inputs,
                           "question": "Compare every original condition, including omitted or unmapped prose, with normalized requirements. Request linked checks for unresolved dependencies."})
            start = end
    local = []
    for index, case in enumerate(document["test_cases"]):
        inputs = [ref(document, f"/test_cases/{index}")]
        requirement_ids = set(case["requirement_ids"])
        source_ids = {mapping["source_requirement_id"] for mapping in document["source_to_canonical_mappings"] if requirement_ids & set(mapping["canonical_requirement_ids"])}
        for field, key, ids in (("requirements", "requirement_id", requirement_ids), ("source_requirements", "source_requirement_id", source_ids)):
            inputs.extend(ref(document, f"/{field}/{offset}") for offset, item in enumerate(document[field]) if item[key] in ids)
        capabilities = {step["operation"].get("capability_id") for step in case["steps"] if step["operation"]}
        inputs.extend(ref(document, f"/operation_capabilities/{offset}") for offset, capability in enumerate(document["operation_capabilities"]) if capability["capability_id"] in capabilities)
        if automation is not None:
            generated = automation["artifacts"]
            relations = [(offset, relation) for offset, relation in enumerate(generated["implementation_relations"]) if relation["case_id"] == case["case_id"]]
            file_ids = {relation["file_id"] for _, relation in relations}
            inputs.extend(ref(automation, f"/artifacts/implementation_relations/{offset}") for offset, _ in relations)
            # Full files include setup/helpers. Oversized indivisible code remains
            # a named gap instead of silently dropping the application boundary.
            for field in ("generated_files", "generated_symbols"):
                inputs.extend(ref(automation, f"/artifacts/{field}/{offset}") for offset, item in enumerate(generated[field]) if item["file_id"] in file_ids)
            for field in ("manual_dispositions", "diagnostics"):
                inputs.append(ref(automation, f"/artifacts/{field}"))
        for source in snapshot["contexts"]:
            inputs.append(raw_ref(source, 0, len(source["content"].encode("utf-8"))))
        local.append({"scope_id": f"local-{case['case_id']}", "kind": "local", "targets": [case["case_id"]], "inputs": inputs,
                      "question": "Check actual behavior, literals, bindings, comparators, setup and helper-to-application boundary; verify manual/blocker reasons. Missing evidence is UNCHECKED."})
    scopes.extend(local)
    if automation is not None:
        generated = automation["artifacts"]
        referenced = {row["file_id"] for row in generated["implementation_relations"]}
        for offset, file in enumerate(generated["generated_files"]):
            if file["file_id"] in referenced:
                continue
            inputs = [ref(automation, f"/artifacts/generated_files/{offset}")]
            inputs.extend(ref(automation, f"/artifacts/generated_symbols/{index}") for index, symbol in enumerate(generated["generated_symbols"]) if symbol["file_id"] == file["file_id"])
            inputs.extend(raw_ref(source, 0, len(source["content"].encode("utf-8"))) for source in snapshot["contexts"])
            scope = {"scope_id": "local-file-" + file["file_id"], "kind": "local", "targets": [file["file_id"]], "inputs": inputs,
                     "question": "Review this support file, its setup/helpers and application boundary. Its interactions with case files must also be checked."}
            local.append(scope)
            scopes.append(scope)
    links = []
    # ponytail: quadratic pairs avoid guessing semantic independence; replace only
    # with a reviewed dependency partition if pair volume becomes the bottleneck.
    for index, scope in enumerate(local):
        for other_index in range(index + 1, len(local)):
            inputs = copy.deepcopy(local[index]["inputs"])
            inputs.extend(item for item in local[other_index]["inputs"] if item not in inputs)
            links.append({"scope_id": f"cross-{index:06d}-{other_index:06d}", "kind": "cross",
                          "targets": [*scope["targets"], *local[other_index]["targets"]], "inputs": inputs,
                          "question": "Compare shared requirements, state and operations across cases; detect inconsistent expectations and interactions."})
    if not links:
        links = [{"scope_id": "cross-baseline", "kind": "cross", "targets": [document["document_id"]],
                  "inputs": [ref(document, "/metadata"), *local[0]["inputs"]],
                  "question": "Check requirement-to-case consistency and identify any missing cross-case dependency requiring original evidence."}]
    scopes.extend(links)
    validate_scope_inputs(snapshot, scopes)
    return scopes


def apply_review_corrections(document: Mapping[str, Any], corrections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply exact non-conflicting text replacements, then validate the whole r2."""
    from tools.canonical_document import require_valid_canonical_document
    from tools.revision_selection import validate_successor

    successor = copy.deepcopy(dict(document))
    seen = set()
    for correction in corrections:
        pointer = correction["path"]
        if correction["correction_kind"] != "MECHANICAL" or not pointer or pointer in seen:
            raise ValueError("review correction is ambiguous or non-mechanical")
        seen.add(pointer)
        parent_pointer, _, key = pointer.rpartition("/")
        parent = _pointer_value(successor, parent_pointer)
        key = int(key) if isinstance(parent, list) else key.replace("~1", "/").replace("~0", "~")
        if parent[key] != correction["before"] or not isinstance(parent[key], str):
            raise ValueError("review correction preimage differs")
        parent[key] = correction["after"]
    successor["revision"] = 2
    successor["parent_sha256"] = review_digest(document)
    require_valid_canonical_document(successor)
    if validate_successor(dict(document), successor):
        raise ValueError("review correction changes identities or machine behavior")
    return successor


def review_output(snapshot: Mapping[str, Any], aggregate: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    """Controller projection of verified part assessments, never a model response."""
    document = snapshot["document"]
    automation = snapshot["automation"]
    findings = copy.deepcopy(aggregate["findings"])
    corrections = copy.deepcopy(aggregate["corrections"])
    successor = None
    if aggregate["unchecked"] or aggregate["diagnostics"]:
        findings.append({"severity": "BLOCKING", "code": "REVIEW_INCOMPLETE", "message": "Required review evidence is incomplete.",
                         "evidence": [aggregate["plan_digest"]], "related_ids": [document["document_id"]]})
    if aggregate["eligible"] and corrections and automation is None:
        try:
            successor = apply_review_corrections(document, corrections)
        except (KeyError, TypeError, ValueError):
            findings.append({"severity": "BLOCKING", "code": "REVIEW_CORRECTION_CONFLICT", "message": "Corrections require a semantic decision or change machine behavior.",
                             "evidence": [aggregate["plan_digest"]], "related_ids": [document["document_id"]]})
    rejected = any(item["severity"] == "BLOCKING" for item in findings)
    verdict = "ТРЕБУЕТ ДОРАБОТКИ" if rejected else "AUTO_FIX_APPLIED" if corrections else "ПРИНЯТО"
    # Existing output contracts reserve findings for rework/correction reports.
    warnings = [item["message"] for item in findings if item["severity"] != "BLOCKING"]
    if verdict == "ПРИНЯТО":
        findings = []
    corrections = [] if rejected else corrections
    projected_corrections = [{key: value for key, value in item.items() if key not in {"path", "before", "after"} and (automation is None or key != "correction_kind")} for item in corrections]
    binding = {"plan_digest": aggregate["plan_digest"], "aggregate_digest": review_digest(aggregate)}
    if automation is None:
        def source(value):
            return {"document_id": value["document_id"], "revision": value["revision"], "document_sha256": review_digest(value)}
        report = {"verdict": verdict, "candidate": source(document), "effective": None if rejected else source(successor or document),
                  "reviewed_case_ids": [case["case_id"] for case in document["test_cases"] if "local-" + case["case_id"] in aggregate["checked_scope_ids"]],
                  "findings": findings, "corrections": projected_corrections}
        artifacts = {"validation_report": report, "review_aggregate": binding}
        if successor is not None and not rejected:
            artifacts["successor_document"] = successor
        return {"schema_version": "6.0.0", "stage": "tc-reviewer", "artifacts": artifacts, "warnings": warnings}
    from tools.automation_validation import implementation_relations_sha256
    generated = automation["artifacts"]
    checked_cases = {case["case_id"] for case in document["test_cases"] if "local-" + case["case_id"] in aggregate["checked_scope_ids"]}
    relations = [row for row in generated["implementation_relations"] if row["case_id"] in checked_cases]
    pairs = {(row["file_id"], row["symbol_id"]) for row in relations}
    checked_files = {row["file_id"] for row in relations} | {row["file_id"] for row in generated["generated_files"] if "local-file-" + row["file_id"] in aggregate["checked_scope_ids"]}
    report = {"source": generated["source"], "automation_revision": generated["automation_revision"],
              "automation_sha256": review_digest(automation), "reviewer_session_id": session_id,
              "reviewed_files": [{"file_id": row["file_id"], "content_digest": row["content_digest"]} for row in generated["generated_files"] if row["file_id"] in checked_files],
              "reviewed_symbol_pairs": [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in generated["generated_symbols"] if (row["file_id"], row["symbol_id"]) in pairs],
              "reviewed_relations_sha256": implementation_relations_sha256(relations),
              "verdict": verdict, "findings": findings, "corrections": projected_corrections}
    return {"schema_version": "6.0.0", "stage": "autotest-reviewer", "artifacts": {"autotest_review": report, "review_aggregate": binding}, "warnings": warnings}
