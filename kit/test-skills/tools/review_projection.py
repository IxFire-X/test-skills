"""Deterministic, ID-anchored text projection of a canonical test document for compact review.

The projection is the only model input of ``compact-v1`` review besides the original
requirement documents.  It is a pure function of the canonical document: the same
document gives the same bytes, any semantic leaf (every field except the service
fields in ``SERVICE_FIELDS``) changes them, and every canonical ID is defined by
exactly one anchor line ``[ID] …``.

Format rules that keep anchors unambiguous:

* an anchor is a line whose first non-blank characters are ``[ID]``;
* every free-text value is written after a ``label: `` on its first line, and each
  further line of the same value starts with ``| ``, so document text can never
  start a line with ``[``;
* literals are compact JSON with sorted keys;
* CRLF inside text is printed as a line break (a lone CR stays visible as ``\r``).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

PROJECTION_VERSION = "review-projection-v1"

# Fields that carry no review meaning: format/lineage service data and physical order
# (order is the order of lines).  Everything else must change the projection.
SERVICE_FIELDS = (
    re.compile(r"^/schema_version$"),
    re.compile(r"^/content_locale$"),
    re.compile(r"^/revision$"),
    re.compile(r"^/parent_sha256$"),
    re.compile(r"(^|/)display_order$"),
    re.compile(r"^/source_requirements/[0-9]+/digest$"),
)

# Canonical IDs: each is defined by exactly one anchor.
ID_FIELDS = ("document_id", "source_requirement_id", "requirement_id", "capability_id", "case_id", "step_id",
             "input_id", "expectation_id", "assertion_id", "blocker_id")

_ANCHOR = re.compile(r"^\s*\[([^\]\s]+)\]")


def is_service_pointer(pointer: str) -> bool:
    return any(pattern.search(pointer) for pattern in SERVICE_FIELDS)


def lit(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean(value: Any) -> str:
    # CRLF of a Windows source is a line break; a lone carriage return stays visible.
    return str(value).replace("\r\n", "\n").replace("\r", "\\r")


def _text(indent: str, label: str, value: Any) -> list[str]:
    lines = _clean(value).split("\n")
    head = f"{indent}{label}: {lines[0]}" if label else f"{indent}{lines[0]}"
    return [head, *(f"{indent}  | {line}" for line in lines[1:])]


def _type(semantic: Mapping[str, Any] | None) -> str:
    if not isinstance(semantic, Mapping):
        return ""
    if semantic.get("kind") == "json":
        rest = {key: value for key, value in semantic.items() if key not in {"kind", "type"}}
        return f":{semantic.get('type')}" + (lit(rest) if rest else "")
    if semantic.get("kind") == "named":
        rest = {key: value for key, value in semantic.items() if key not in {"kind", "name", "representation"}}
        return f":{semantic.get('name')}/{semantic.get('representation')}" + (lit(rest) if rest else "")
    return ":" + lit(semantic)


def _types(provenance: Sequence[str] | None) -> str:
    return f" [types: {'; '.join(_clean(item) for item in provenance)}]" if provenance else ""


def _rest(value: Mapping[str, Any], handled: Iterable[str]) -> str:
    """Fields the renderer does not know yet stay visible instead of being dropped."""
    rest = {key: item for key, item in value.items() if key not in set(handled)}
    return f" +{lit(rest)}" if rest else ""


def _source(source: Mapping[str, Any]) -> str:
    kind = source.get("kind")
    if kind == "literal":
        text = lit(source.get("value"))
        handled = ("kind", "value")
    elif kind == "step_output":
        text = f"{source.get('step_id')}.{source.get('output_id')}"
        handled = ("kind", "step_id", "output_id")
    elif kind in {"fixture", "environment"}:
        text = f"{kind}:{source.get('name')}"
        handled = ("kind", "name", "semantic_type", "type_provenance")
    elif kind == "secret_handle":
        text = f"secret:{source.get('safe_label')}#{source.get('handle')}"
        handled = ("kind", "safe_label", "handle", "semantic_type", "type_provenance")
    elif kind == "regex":
        text = f"regex[{source.get('dialect')}]:/{_clean(source.get('pattern'))}/"
        handled = ("kind", "dialect", "pattern")
    elif kind == "schema_ref":
        text = f"schema:{source.get('uri')} {source.get('sha256')} draft={source.get('draft')}"
        if source.get("provenance"):
            text += f" [src: {'; '.join(_clean(item) for item in source['provenance'])}]"
        handled = ("kind", "uri", "sha256", "draft", "provenance")
    else:
        return lit(source)
    if kind in {"fixture", "environment", "secret_handle"}:
        text += _type(source.get("semantic_type")) + _types(source.get("type_provenance"))
    return text + _rest(source, handled)


def _observed(actual: Mapping[str, Any]) -> str:
    kind = actual.get("kind")
    if kind == "http_status":
        text, handled = "http_status", ("kind",)
    elif kind in {"http_header", "project_result"}:
        text, handled = f"{kind}:{actual.get('name')}", ("kind", "name")
    elif kind == "http_body":
        text = f"http_body:{actual.get('pointer')}" + _type(actual.get("semantic_type")) + _types(actual.get("type_provenance"))
        handled = ("kind", "pointer", "semantic_type", "type_provenance")
    elif kind == "step_output":
        text, handled = f"{actual.get('step_id')}.{actual.get('output_id')}", ("kind", "step_id", "output_id")
    else:
        return lit(actual)
    return text + _rest(actual, handled)


def _target(target: Mapping[str, Any]) -> str:
    location = target.get("location")
    name = target.get("pointer") if location == "body" else target.get("name")
    text = f"{location}:{name}" + (" (sensitive)" if target.get("sensitive") else "")
    return text + _rest(target, ("location", "name", "pointer", "sensitive"))


def _operation(operation: Mapping[str, Any] | None) -> str:
    if operation is None:
        return "none"
    if operation.get("kind") == "project_action":
        return f"{operation.get('capability_id')}" + _rest(operation, ("kind", "capability_id"))
    if operation.get("kind") == "http":
        base = operation.get("base_url_source") or {}
        text = (f"http {operation.get('method')} {operation.get('path')} [{operation.get('binding_profile')}] "
                f"base_url={base.get('kind')}:{base.get('name')}")
        if base.get("provenance"):
            text += f" [src: {'; '.join(_clean(item) for item in base['provenance'])}]"
        text += _rest(base, ("kind", "name", "provenance"))
        return text + _rest(operation, ("kind", "method", "path", "binding_profile", "base_url_source"))
    return lit(operation)


def _management(management: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(management, Mapping):
        return []
    fields = [f"{key}={lit(value)}" for key, value in sorted(management.items()) if value not in (None, [], {}, "")]
    # An empty string differs from null: say so instead of hiding it.
    fields.extend(f"{key}=\"\"" for key, value in sorted(management.items()) if value == "")
    return [f"  management: {'; '.join(fields)}"] if fields else []


def case_text(case: Mapping[str, Any]) -> str:
    out = [f"[{case.get('case_id')}] {_clean(case.get('title'))}".split("\n")[0]]
    title_lines = _clean(case.get("title")).split("\n")
    out.extend(f"  | {line}" for line in title_lines[1:])
    out.append(f"  priority: {case.get('priority')} · categories: {', '.join(case.get('categories') or [])}")
    out.append(f"  requirements: {', '.join(case.get('requirement_ids') or [])}")
    out.extend(_text("  ", "objective", case.get("objective")))
    for item in case.get("preconditions") or []:
        out.extend(_text("  ", "pre", item))
    out.extend(_management(case.get("management")))
    extra = _rest(case, ("case_id", "title", "priority", "categories", "requirement_ids", "objective", "preconditions",
                         "management", "steps", "display_order"))
    if extra:
        out.append(f"  extra:{extra}")
    for step in case.get("steps") or []:
        out.extend(_step(step))
    return "\n".join(out)


def _step(step: Mapping[str, Any], *, dense: bool = False) -> list[str]:
    out = _text("  ", f"[{step.get('step_id')}] action", step.get("action"))
    if not dense:
        out.extend(_text("    ", "data", step.get("test_data")))
    elif not step.get("inputs"):
        # With inputs, the human Test Data restates them; the code implements the inputs.
        out.extend(_text("    ", "data", _compact_data(step.get("test_data"))))
    out.append(f"    call: {_operation(step.get('operation'))}")
    for item in step.get("inputs") or []:
        line = f"    [{item.get('input_id')}] {_target(item.get('target') or {})} = {_source(item.get('source') or {})}"
        line += " " + _type(item.get("semantic_type")) + _types(item.get("type_provenance"))
        line += _rest(item, ("input_id", "target", "source", "semantic_type", "type_provenance", "display_order"))
        out.append(line)
    if step.get("manual_only") or step.get("manual_reason") is not None:
        out.extend(_text("    ", f"manual_only={lit(step.get('manual_only'))} reason", step.get("manual_reason")))
    for blocker in step.get("automation_blockers") or []:
        head = f"    [{blocker.get('blocker_id')}] blocker {blocker.get('code')} at {blocker.get('field_path')}"
        out.extend(_text("", head + " reason", blocker.get("reason")))
        if blocker.get("provenance"):
            out.append(f"      src: {'; '.join(_clean(item) for item in blocker['provenance'])}")
        rest = _rest(blocker, ("blocker_id", "code", "field_path", "reason", "provenance"))
        if rest:
            out.append(f"      extra:{rest}")
    if dense and step.get("outputs"):
        out.append(f"    out: {', '.join(_output_token(output) for output in step['outputs'])}")
    for output in [] if dense else step.get("outputs") or []:
        line = f"    out {output.get('output_id')} ← {_observed(output.get('source') or {})}"
        line += _type(output.get("semantic_type")) + _types(output.get("type_provenance"))
        line += _rest(output, ("output_id", "source", "semantic_type", "type_provenance", "display_order"))
        out.append(line)
    for expectation in step.get("expectations") or []:
        out.extend(_text("    ", f"[{expectation.get('expectation_id')}] expected", expectation.get("text")))
        for assertion in expectation.get("assertions") or []:
            expected = assertion.get("expected")
            line = f"      [{assertion.get('assertion_id')}] {_observed(assertion.get('actual') or {})} {assertion.get('operator')}"
            if expected is not None or "expected" in assertion:
                line += " " + (_source(expected) if isinstance(expected, Mapping) else lit(expected))
            line += _rest(assertion, ("assertion_id", "actual", "operator", "expected", "display_order"))
            out.append(line)
        rest = _rest(expectation, ("expectation_id", "text", "assertions", "display_order"))
        if rest:
            out.append(f"      extra:{rest}")
    rest = _rest(step, ("step_id", "action", "test_data", "operation", "inputs", "manual_only", "manual_reason",
                        "automation_blockers", "outputs", "expectations", "display_order"))
    if rest:
        out.append(f"    extra:{rest}")
    return out


def _compact_data(value: Any) -> Any:
    """Test data that is a JSON document, on one line: whitespace between tokens dropped, tokens byte for byte."""
    if not isinstance(value, str) or not value.lstrip().startswith(("{", "[")):
        return value
    try:
        json.loads(value)
    except ValueError:
        return value
    out, in_string, escaped = [], False, False
    for char in value:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            out.append(char)
            in_string = True
        elif char not in " \t\r\n":
            out.append(char)
    return "".join(out)


def _output_token(output: Mapping[str, Any]) -> str:
    source = output.get("source") or {}
    if source.get("kind") == "project_result" and source.get("name") == output.get("output_id") and set(source) == {"kind", "name"}:
        text = str(output.get("output_id"))
    else:
        text = f"{output.get('output_id')}←{_observed(source)}"
    text += _type(output.get("semantic_type")) + _types(output.get("type_provenance"))
    return text + _rest(output, ("output_id", "source", "semantic_type", "type_provenance", "display_order"))


def automation_case_text(case: Mapping[str, Any]) -> str:
    """The case as the automation reviewer needs it: same anchors as ``case_text``, denser lines.

    Everything generated code must implement stays exact (operations, inputs, outputs,
    expectations, assertions, blockers).  Test data that is JSON goes on one line,
    outputs of a step share one line (``name:type`` when it is the project result of the
    same name), and management, priority and categories — case-review fields — are left out.
    """
    out = [f"[{case.get('case_id')}] {_clean(case.get('title'))}".split("\n")[0]]
    out.extend(f"  | {line}" for line in _clean(case.get("title")).split("\n")[1:])
    out.append(f"  requirements: {', '.join(case.get('requirement_ids') or [])}")
    out.extend(_text("  ", "objective", case.get("objective")))
    for item in case.get("preconditions") or []:
        out.extend(_text("  ", "pre", item))
    extra = _rest(case, ("case_id", "title", "priority", "categories", "requirement_ids", "objective", "preconditions",
                         "management", "steps", "display_order"))
    if extra:
        out.append(f"  extra:{extra}")
    for step in case.get("steps") or []:
        out.extend(_step(step, dense=True))
    return "\n".join(out)


def _params(rows: Sequence[Mapping[str, Any]] | None, *, optional: bool) -> str:
    parts = []
    for row in rows or []:
        mark = "?" if optional and not row.get("required") else ""
        parts.append(f"{row.get('name')}{mark}{_type(row.get('semantic_type'))}"
                     + _rest(row, ("name", "required", "semantic_type")))
    return ", ".join(parts)


def capability_signature(capability: Mapping[str, Any]) -> str:
    return (f"[{capability.get('capability_id')}] {capability.get('action')}({_params(capability.get('arguments'), optional=True)})"
            f" → {_params(capability.get('results'), optional=False) or '—'} · adapter={capability.get('adapter')}")


def capability_text(capability: Mapping[str, Any]) -> str:
    out = [capability_signature(capability)]
    for line in capability.get("provenance") or []:
        out.extend(_text("  ", "src", line))
    rest = _rest(capability, ("capability_id", "action", "arguments", "results", "adapter", "provenance"))
    if rest:
        out.append(f"  extra:{rest}")
    return "\n".join(out)


def requirement_text(requirement: Mapping[str, Any]) -> str:
    out = _text("", f"[{requirement.get('requirement_id')}]", requirement.get("text"))
    out[0] = out[0].replace("]: ", "] ", 1)
    if requirement.get("provenance"):
        out.append(f"  src: {'; '.join(_clean(item) for item in requirement['provenance'])}")
    rest = _rest(requirement, ("requirement_id", "text", "provenance", "display_order"))
    if rest:
        out.append(f"  extra:{rest}")
    return "\n".join(out)


def source_requirement_text(requirement: Mapping[str, Any]) -> str:
    out = [f"[{requirement.get('source_requirement_id')}] src: {'; '.join(_clean(item) for item in requirement.get('provenance') or [])}"]
    out.extend(_text("  ", "text", requirement.get("text")))
    rest = _rest(requirement, ("source_requirement_id", "text", "provenance", "display_order", "digest"))
    if rest:
        out.append(f"  extra:{rest}")
    return "\n".join(out)


def mapping_text(document: Mapping[str, Any]) -> str:
    rows = []
    for mapping in document.get("source_to_canonical_mappings") or []:
        rows.append(f"{mapping.get('source_requirement_id')} → {', '.join(mapping.get('canonical_requirement_ids') or [])}"
                    + _rest(mapping, ("source_requirement_id", "canonical_requirement_ids")))
    return "\n".join(rows)


def header_text(document: Mapping[str, Any]) -> str:
    metadata = document.get("metadata") or {}
    subject = metadata.get("subject") or {}
    if subject.get("kind") == "http_endpoint":
        subject_text = f"{subject.get('method')} {subject.get('path')}"
    else:
        subject_text = _clean(subject.get("name"))
    subject_text += _rest(subject, ("kind", "name", "method", "path")) + f" ({subject.get('kind')})"
    out = [f"[{document.get('document_id')}] {subject_text}",
           f"  project: {_clean(metadata.get('project'))} · author: {_clean(metadata.get('author'))} · date: {metadata.get('date')}",
           f"  documentation: {', '.join(_clean(item) for item in metadata.get('documentation') or [])}"]
    rest = _rest(metadata, ("subject", "project", "author", "date", "documentation"))
    rest += _rest(document, ("document_id", "metadata", "schema_version", "content_locale", "revision", "parent_sha256",
                             "operation_capabilities", "source_requirements", "requirements", "source_to_canonical_mappings", "test_cases"))
    if rest:
        out.append(f"  extra:{rest}")
    return "\n".join(out)


def source_document_text(number: int, source: Mapping[str, Any]) -> str:
    """An original requirement document with line numbers: refs are ``SRC-n`` or ``SRC-n:L12``."""
    lines = str(source["content"]).replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    width = len(str(len(lines)))
    return "\n".join([f"[SRC-{number}] {source['path']} ({len(lines)} строк)",
                      *(f"L{index:0{width}d}| {line}" for index, line in enumerate(lines, start=1))])


def build_projection(document: Mapping[str, Any]) -> dict[str, Any]:
    """Every canonical section once, in document order; the digest binds the canonical document."""
    from tools.review_parts import review_digest

    sections = [{"anchor": document.get("document_id"), "kind": "document", "text": header_text(document)}]
    sections.extend({"anchor": item.get("source_requirement_id"), "kind": "source_requirement", "text": source_requirement_text(item)}
                    for item in document.get("source_requirements") or [])
    sections.extend({"anchor": item.get("requirement_id"), "kind": "requirement", "text": requirement_text(item)}
                    for item in document.get("requirements") or [])
    sections.append({"anchor": None, "kind": "mapping", "text": mapping_text(document)})
    sections.extend({"anchor": item.get("capability_id"), "kind": "capability", "text": capability_text(item)}
                    for item in document.get("operation_capabilities") or [])
    sections.extend({"anchor": item.get("case_id"), "kind": "case", "text": case_text(item)}
                    for item in document.get("test_cases") or [])
    projection = {"version": PROJECTION_VERSION, "document_digest": review_digest(document), "sections": sections}
    projection["digest"] = review_digest(projection)
    return projection


def projection_text(projection: Mapping[str, Any]) -> str:
    return "\n\n".join(section["text"] for section in projection["sections"]) + "\n"


def anchors(text: str) -> list[str]:
    """Anchor definitions in order (duplicates kept, so a caller can detect them)."""
    return [match.group(1) for match in (_ANCHOR.match(line) for line in text.split("\n")) if match]


def canonical_ids(document: Mapping[str, Any]) -> list[str]:
    """Every canonical ID in document order."""
    ids = [document.get("document_id")]
    ids.extend(item.get("source_requirement_id") for item in document.get("source_requirements") or [])
    ids.extend(item.get("requirement_id") for item in document.get("requirements") or [])
    ids.extend(item.get("capability_id") for item in document.get("operation_capabilities") or [])
    for case in document.get("test_cases") or []:
        ids.append(case.get("case_id"))
        for step in case.get("steps") or []:
            ids.append(step.get("step_id"))
            ids.extend(item.get("input_id") for item in step.get("inputs") or [])
            ids.extend(item.get("blocker_id") for item in step.get("automation_blockers") or [])
            for expectation in step.get("expectations") or []:
                ids.append(expectation.get("expectation_id"))
                ids.extend(item.get("assertion_id") for item in expectation.get("assertions") or [])
    return [item for item in ids if isinstance(item, str)]


def case_anchor_ids(case: Mapping[str, Any]) -> list[str]:
    """IDs a case defines (its own anchors): a coverage row of a case area cites one of them."""
    return anchors(case_text(case))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
