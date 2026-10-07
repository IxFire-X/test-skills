"""Build a 5.0.0 context-marker envelope from authorized docs and source handles."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from tools.json_cli import JsonArgumentParser, emit_error
from tools.project_inventory import DEFAULT_CONTEXT_LIMITS, context_limits, redact_text
from tools.schema_validation import schema_diagnostics

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "context-marker-output.schema.json"
_DOCS_MAX_FILE = DEFAULT_CONTEXT_LIMITS["docs_file_bytes"]
_DOCS_MAX_TOTAL = DEFAULT_CONTEXT_LIMITS["docs_total_bytes"]

REQ_ID_RE = re.compile(r"\b(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b", re.I)
HEADING_RE = re.compile(r"^(?:#{1,6}\s+[^\n]+|(?:[-*]\s+)?(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b[^\n]*)", re.M | re.I)
_BUILTIN_ID = r"(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b"


def compile_id_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """The project's explicit requirement ID (``.skillsrc`` ``requirements.id_pattern``), or None.

    The pattern marks an ID at the start of a line, exactly like the built-in ``REQ-``/``AC-`` IDs;
    a pattern that does not compile or matches the empty string is rejected.
    """
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
        raise ValueError("requirements.id_pattern must be a non-empty string of at most 200 characters")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"requirements.id_pattern does not compile: {error}") from error
    if compiled.fullmatch("") is not None or compiled.match("") is not None:
        raise ValueError("requirements.id_pattern must not match the empty string")
    return compiled


def _heading_regex(id_re: re.Pattern[str] | None) -> re.Pattern[str]:
    if id_re is None:
        return HEADING_RE
    return re.compile(r"^(?:#{1,6}\s+[^\n]+|(?:[-*]\s+)?(?:" + _BUILTIN_ID + r"|(?-i:" + id_re.pattern + r"))[^\n]*)", re.M | re.I)


def explicit_requirement_id(title: str, id_re: re.Pattern[str] | None = None) -> str | None:
    """The explicit ID a section title starts with (bullet and bold marks ignored), without trailing punctuation."""
    head = re.sub(r"^(?:[-*+]\s+)?(?:\*\*)?", "", title or "")
    match = re.match(_BUILTIN_ID, head, re.I) or (id_re.match(head) if id_re is not None else None)
    if match is None or not match.group(0).strip():
        return None
    return match.group(0).strip().rstrip(".:)").strip() or None
API_PATH_RE = re.compile(r"""['"](/api/[^'"]+)['"]""")
FLASK_ROUTE_RE = re.compile(
    r"""@(?:app|router|bp|api)\.(?:get|post|put|patch|delete|route)\(\s*['"]([^'"]+)['"]""",
    re.I,
)


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().strip(".:")


def _compare_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


# Sections that describe the document itself rather than required behaviour.
_REFERENCE_TITLES = frozenset({
    "глоссарий", "термины", "термины и определения", "определения", "словарь терминов", "список терминов",
    "оглавление", "содержание",
    "glossary", "terms", "terminology", "terms and definitions", "definitions", "table of contents", "contents", "toc",
})
_TITLE_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)")
_TOC_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*\[[^\]]+\]\(#[^)]*\)\s*$")


def _section_kind(section: str, title: str, id_re: re.Pattern[str] | None = None) -> str:
    """Classify one delimited section; only requirement/flow rows need a test case."""
    if REQ_ID_RE.match(title) or (id_re is not None and id_re.match(title)):
        return "requirement"
    lines = section.splitlines()
    if not lines[0].lstrip().startswith("#"):
        return "flow"
    body = [line for line in lines[1:] if line.strip()]
    if not body:
        # A heading that only groups sub-headings carries no requirement text.
        return "structure"
    plain = _TITLE_NUMBERING.sub("", title).casefold()
    if plain in _REFERENCE_TITLES or all(_TOC_LINE.match(line) for line in body):
        return "reference"
    return "flow"


def extract_inventory(analytics: str, id_pattern: str | re.Pattern[str] | None = None) -> list[dict[str, Any]]:
    """Partition authorized requirement prose without dropping unlabelled content.

    Every section is returned in document order. ``kind`` is ``requirement`` or
    ``flow`` for text a test case must cover, ``structure`` for a heading without
    its own text and ``reference`` for a glossary or table of contents. Deeper
    semantic classification belongs to context-marker/reviewer.  ``chain`` is the
    titles of the enclosing Markdown headings and the section's own title; with
    ``id_pattern`` a line starting with the project's ID opens a section like a
    built-in ``REQ-`` ID does.
    """
    text = analytics or ""
    if not text.strip():
        return []
    id_re = id_pattern if isinstance(id_pattern, re.Pattern) else compile_id_pattern(id_pattern)
    found: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    boundaries = sorted({0, len(text), *(match.start() for match in _heading_regex(id_re).finditer(text))})
    for start, stop in zip(boundaries, boundaries[1:]):
        section = text[start:stop].strip()
        if section:
            first = section.splitlines()[0]
            title = _norm(first.lstrip("# "))
            heading = re.match(r"^(#{1,6})\s", first)
            if heading:
                level = len(heading.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                chain = [name for _level, name in stack] + [title]
                stack.append((level, title))
            else:
                chain = [name for _level, name in stack] + [title]
            found.append({"kind": _section_kind(section, title, id_re), "title": title, "text": section, "chain": chain,
                          "explicit_id": explicit_requirement_id(title, id_re)})
    return found


_REQUIREMENT_KINDS = frozenset({"requirement", "flow"})


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _source_requirement_id(index: int) -> str:
    """Return a controller-owned sequential source ID after stable normalization."""
    return f"SREQ-{index:04d}"


def _confined(project: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
        resolved.relative_to(project.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def extract_handles(text: str) -> list[str]:
    paths: list[str] = []
    for match in API_PATH_RE.findall(text):
        paths.append(match.split("?")[0])
    for match in FLASK_ROUTE_RE.findall(text):
        paths.append(match if match.startswith("/") else "/" + match)
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


class RequirementConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# OpenSpec may live in any project directory: ``**/openspec/specs`` (decision 18).
# Groups: openspec root prefix, "specs", baseline capability, change name, delta capability.
_OPENSPEC_PATH = re.compile(r"^(?:(.+?)/)?openspec/(?:(specs)/(.+)|changes/(?!archive/)([^/]+)/specs/(.+))/spec\.md$")
_OPENSPEC_ARCHIVE = re.compile(r"^(?:.+?/)?openspec/changes/archive/")
# Heading keywords are matched without regard to case or spacing: "###Requirement:",
# "## Added Requirements" and "#### scenario:" are all valid.
_OPENSPEC_HEADING = re.compile(
    r"^[ \t]{0,3}(?:"
    r"(?P<requirement>###[ \t]*Requirement[ \t]*:[ \t]*(?P<requirement_name>[^\n]+?))"
    r"|(?P<scenario>####[ \t]*Scenario[ \t]*:[ \t]*(?P<scenario_name>[^\n]+?))"
    r"|(?P<section>##(?!#)[ \t]*(?P<section_title>[^\n]+?))"
    r")[ \t\r]*$",
    re.M | re.I,
)
_OPENSPEC_DELTA_SECTION = re.compile(r"^(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements$", re.I)
_OPENSPEC_RENAME_PAIR = re.compile(r"^\s*(?:[-*+]\s+)?(FROM|TO)\s*:\s*`?###[ \t]*Requirement[ \t]*:[ \t]*(.+?)`?\s*$", re.M | re.I)
_OPENSPEC_REQUIREMENT_LINE = re.compile(r"^[ \t]{0,3}###[ \t]*Requirement[ \t]*:[^\n]*", re.I)


def is_openspec_document(path: str) -> bool:
    """True for a baseline, delta or archived OpenSpec spec in any project directory."""
    normalized = path.replace("\\", "/")
    return bool(_OPENSPEC_ARCHIVE.match(normalized) or _OPENSPEC_PATH.fullmatch(normalized))


def _openspec_rows(entries: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Compose only standard spec documents explicitly present in authorized inputs."""
    baseline: dict[tuple[str, str, str], dict[str, Any]] = {}
    operations: list[tuple[str, tuple[str, str, str], dict[str, Any]]] = []
    renames: list[tuple[tuple[str, str, str], str, str, str]] = []
    warnings: list[str] = []
    changes: dict[str, set[str]] = {}
    for entry in sorted(entries, key=lambda row: row["path"]):
        path = entry["path"].replace("\\", "/")
        if _OPENSPEC_ARCHIVE.match(path):
            warnings.append(f"{path} — {entry['sha256']}; historical archived context, not reapplied to current requirements")
            continue
        match = _OPENSPEC_PATH.fullmatch(path)
        if not match:
            continue
        if entry["sha256"] != "sha256:" + hashlib.sha256(entry["content"].encode("utf-8")).hexdigest():
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"source digest does not match content: {path}")
        prefix, is_baseline, capability, change, delta_capability = match.groups()
        # Each openspec/ directory is its own specification root with its own capabilities.
        spec_root = prefix or ""
        if change:
            changes.setdefault(spec_root, set()).add(change)
        capability = capability or delta_capability
        # A leading BOM is not content; masking keeps line numbers intact.
        text, redactions = redact_text(entry["content"].removeprefix("﻿"))
        warnings.extend(_redaction_warnings(path, redactions))
        headings = list(_OPENSPEC_HEADING.finditer(text))
        section = "requirements" if is_baseline else ""
        for index, heading in enumerate(headings):
            if heading.group("section") is not None:
                section = re.sub(r"\s+", " ", heading.group("section_title")).strip().casefold()
                if section == "renamed requirements":
                    stop = next((item.start() for item in headings[index + 1:] if item.group("section") is not None), len(text))
                    pairs = [(kind.upper(), name) for kind, name in _OPENSPEC_RENAME_PAIR.findall(text[heading.end():stop])]
                    if not pairs or len(pairs) % 2 or any(pairs[i][0] != "FROM" or pairs[i + 1][0] != "TO" for i in range(0, len(pairs), 2)):
                        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"unpaired rename: {path}")
                    for position in range(0, len(pairs), 2):
                        renames.append(((spec_root, capability, pairs[position][1].strip()), pairs[position + 1][1].strip(), path, entry["sha256"]))
                continue
            if heading.group("requirement") is None:
                continue
            name = heading.group("requirement_name").strip()
            stop = next((item.start() for item in headings[index + 1:] if item.group("scenario") is None), len(text))
            block = text[heading.start():stop].strip()
            scenarios = [(item.group("scenario_name").strip(), text.count("\n", 0, item.start()) + 1) for item in headings[index + 1:] if heading.start() < item.start() < stop and item.group("scenario") is not None]
            line = text.count("\n", 0, heading.start()) + 1
            row = {"capability": capability, "name": name, "text": block, "path": path, "digest": entry["sha256"], "line": line, "scenarios": scenarios, "rename_provenance": []}
            key = (spec_root, capability, name)
            if is_baseline:
                if section != "requirements" or key in baseline:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"duplicate or misplaced requirement: {path}:{line} — {name}")
                baseline[key] = row
            else:
                delta = _OPENSPEC_DELTA_SECTION.fullmatch(section)
                operation = delta.group(1).upper() if delta else ""
                if operation not in {"ADDED", "MODIFIED", "REMOVED"}:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"requirement has no delta operation: {path}:{line} — {name}")
                operations.append((operation, key, row))
    if any(len(names) > 1 for names in changes.values()):
        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", "authorize one selected OpenSpec change, not multiple pending changes")
    for key, new_name, path, digest in renames:
        new_key = (key[0], key[1], new_name)
        if key not in baseline or new_key in baseline:
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"rename does not match baseline: {path} — {key[2]} -> {new_name}")
        row = baseline.pop(key)
        row["name"] = new_name
        row["text"] = _OPENSPEC_REQUIREMENT_LINE.sub(lambda _: "### Requirement: " + new_name, row["text"], count=1)
        row["rename_provenance"] = [f"{path} — RENAMED Requirement: {key[2]} -> {new_name}", f"{path} — {digest}"]
        baseline[new_key] = row
    seen: set[tuple[str, str, str]] = set()
    for operation, key, row in operations:
        if key in seen or (operation == "ADDED" and key in baseline) or (operation != "ADDED" and key not in baseline):
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"{operation} does not match baseline: {row['path']}:{row['line']} — {key[2]}")
        seen.add(key)
        if operation == "REMOVED":
            del baseline[key]
        else:
            if key in baseline:
                row["rename_provenance"] = baseline[key]["rename_provenance"]
            baseline[key] = row
    rows = sorted(baseline.values(), key=lambda row: (row["path"], row["line"], row["name"]))
    for row in rows:
        if not row["scenarios"]:
            warnings.append(f"{row['path']}:{row['line']} — Requirement: {row['name']}; missing: scenario/observable outcome; blocks: complete scenario coverage; question: which conditions and result are required?")
        if len({name for name, _ in row["scenarios"]}) != len(row["scenarios"]):
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"duplicate scenario: {row['path']} — {row['name']}")
    return rows, warnings


def _redaction_warnings(path: str, redactions: list[dict[str, Any]]) -> list[str]:
    """Value-free receipt lines for masked secret-like text in an authorized document."""
    return [
        f"{path}:{item['line']} — [REDACTED:{item['scanner_rule_id']}]; secret-like line masked, not a requirement gap"
        for item in redactions
    ]


def _openspec_marks(row: dict[str, Any]) -> list[str]:
    prefix = f"capability={row['capability']}; ### Requirement: {row['name']}"
    marks = [f"{row['path']}:{row['line']} — {prefix}"]
    marks.extend(f"{row['path']}:{line} — {prefix}; #### Scenario: {name}" for name, line in row["scenarios"])
    return marks


def openspec_diagnostics(context: dict[str, Any], docs_snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    """Reconcile final identities and origins; semantic body review is still required."""
    expected, warnings = _openspec_rows(docs_snapshot)
    marks = {mark: row for row in expected for mark in _openspec_marks(row)}
    actual: set[str] = set()
    diagnostics: list[dict[str, str]] = []
    for warning in warnings:
        if "; missing:" in warning and warning not in (context.get("warnings") or []):
            diagnostics.append({"path": warning.split(" — ")[0], "code": "OPENSPEC_GAP_MISSING", "message": warning})
    for item in (context.get("artifacts") or {}).get("analytics_documentation", {}).get("requirements") or []:
        provenance = item.get("provenance") or []
        identities = [mark for mark in provenance if " — capability=" in mark and "; ### Requirement:" in mark]
        if not identities and any(_OPENSPEC_PATH.fullmatch(mark.split(" — ")[0].split(":")[0]) for mark in provenance):
            diagnostics.append({"path": str(item.get("source_requirement_id", "")), "code": "OPENSPEC_EXTRA", "message": f"{provenance[0]} — normalized OpenSpec row has no requirement/scenario identity"})
        for mark in provenance:
            if " — capability=" not in mark or "; ### Requirement:" not in mark:
                continue
            actual.add(mark)
            row = marks.get(mark)
            if row and (item.get("digest") != row["digest"] or f"{row['path']} — {row['digest']}" not in provenance or not set(row["rename_provenance"]).issubset(provenance)):
                diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_SOURCE_MISMATCH", "message": f"{mark} — normalized origin must retain exact source digest and rename provenance"})
    for mark in sorted(marks.keys() - actual):
        diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_MISSING", "message": f"{mark} — missing from normalized requirement/scenario set"})
    for mark in sorted(actual - marks.keys()):
        diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_EXTRA", "message": f"{mark} — absent from final selected specification"})
    return diagnostics


def grounding_diagnostics(context: dict[str, Any], canonical: dict[str, Any]) -> list[dict[str, str]]:
    ctx = list((context.get("artifacts") or {}).get("analytics_documentation", {}).get("requirements") or [])
    can = list(canonical.get("source_requirements") or [])
    rows: list[dict[str, str]] = []

    def tuple_of(item: dict[str, Any]) -> tuple[str, str, tuple[str, ...], str]:
        return (
            str(item.get("source_requirement_id") or ""),
            str(item.get("text") or ""),
            tuple(item.get("provenance") or []),
            str(item.get("digest") or ""),
        )

    if [tuple_of(item) for item in ctx] != [tuple_of(item) for item in can]:
        rows.append({"path": "/source_requirements", "code": "UNGROUNDED", "message": "canonical source requirements must match context id, text, provenance, and order"})
    allowed = {item.get("source_requirement_id") for item in ctx}
    mapped: set[str] = set()
    for mapping in canonical.get("source_to_canonical_mappings") or []:
        source_id = mapping.get("source_requirement_id")
        if source_id not in allowed:
            rows.append({"path": "/source_to_canonical_mappings", "code": "UNGROUNDED", "message": f"unknown source requirement {source_id}"})
        else:
            mapped.add(source_id)
    for index, case in enumerate(canonical.get("test_cases") or []):
        for req in case.get("requirement_ids") or []:
            if req not in {item.get("requirement_id") for item in canonical.get("requirements") or []}:
                rows.append({"path": f"/test_cases/{index}/requirement_ids", "code": "UNGROUNDED", "message": f"unknown canonical requirement {req}"})
    for item in ctx:
        req = item.get("source_requirement_id")
        if req and req not in mapped:
            rows.append({"path": f"/source_requirements/{req}", "code": "UNGROUNDED", "message": f"{req} is not mapped to a canonical requirement"})
    return rows


def uncovered_requirements(context: dict[str, Any], canonical: dict[str, Any]) -> list[str]:
    return sorted({row["message"].split()[0] for row in grounding_diagnostics(context, canonical) if row["code"] == "UNGROUNDED" and row["message"].endswith("is not mapped to a canonical requirement")})


def build_context(
    project: Path,
    docs: list[Path] | None = None,
    source_files: list[Path] | None = None,
    *,
    docs_snapshot: list[dict[str, str]] | None = None,
    source_snapshot: list[dict[str, str]] | None = None,
    max_file_bytes: int | None = None,
    max_total_bytes: int | None = None,
    id_pattern: str | None = None,
) -> dict[str, Any]:
    """Build the envelope. Document size limits come from the explicit arguments,
    then ``limits`` in the project's ``.skillsrc``, then the 256 KiB / 1 MiB defaults.
    The explicit requirement ID comes from ``id_pattern``, else ``requirements.id_pattern``
    of the project's ``.skillsrc``; without one the split is the built-in one."""
    project = project.resolve()
    limits = _project_limits(project)
    if id_pattern is None:
        id_pattern = _project_id_pattern(project)
    file_limit = max_file_bytes if max_file_bytes is not None else limits["docs_file_bytes"]
    total_limit = max_total_bytes if max_total_bytes is not None else limits["docs_total_bytes"]
    if type(file_limit) is not int or type(total_limit) is not int or file_limit <= 0 or total_limit <= 0:
        raise ValueError("docs limits must be positive integers")
    if docs is not None and docs_snapshot is not None:
        raise ValueError("cannot pass docs and docs_snapshot together")
    if source_files and source_snapshot:
        raise ValueError("cannot pass source_files and source_snapshot together")
    requirements: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_docs = 0
    doc_entries: list[dict[str, str]] = []
    if docs_snapshot is None:
        for doc in docs or []:
            confined = _confined(project, doc if doc.is_absolute() else project / doc)
            if confined is None or not confined.is_file():
                raise ValueError(f"docs path must stay inside the project: {doc}")
            with confined.open("rb") as stream:
                data = stream.read(file_limit + 1)
            if len(data) > file_limit:
                raise RequirementConflict("NEED_DOCS_LIMIT", f"NEED_DOCS_LIMIT: docs file exceeds {file_limit} bytes: {doc}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"docs file is not UTF-8: {doc}") from error
            doc_entries.append(
                {
                    "path": confined.relative_to(project).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "content": text,
                }
            )
    else:
        doc_entries = docs_snapshot
    deduped_rows, openspec_provenance, scan_warnings, _identity = scan_documents(doc_entries, file_limit, total_limit, id_pattern)
    warnings.extend(scan_warnings)
    for index, (rel, digest, snippet, title) in enumerate(deduped_rows, start=1):
        req_id = _source_requirement_id(index)
        # The mark names the file and section.  The requirement text itself lives in ``text``
        # and is bound by ``digest``; repeating it here doubled every requirement in every prompt.
        mark = f"{rel} — {_nfc(title)}"
        digest_mark = f"{rel} — {digest}"
        item = {
            "source_requirement_id": req_id,
            "display_order": len(requirements) + 1,
            "text": snippet,
            "provenance": [mark, digest_mark] + openspec_provenance.get((rel, digest, snippet, title), []),
            "digest": digest,
        }
        requirements.append(item)
    sources: list[str] = []
    snapshot_entries = source_snapshot if source_snapshot is not None else None
    if snapshot_entries is None:
        for path in source_files or []:
            confined = _confined(project, path if path.is_absolute() else project / path)
            if confined is None or not confined.is_file():
                continue
            rel = confined.relative_to(project).as_posix()
            text = confined.read_text(encoding="utf-8", errors="replace")
            snapshot_entries = (snapshot_entries or []) + [{"path": rel, "sha256": "", "content": text}]
    for entry in snapshot_entries or []:
        rel = str(entry.get("path") or "")
        text = str(entry.get("content") or "")
        handles = extract_handles(text)
        if not handles:
            sources.append(f"{rel} — source observed")
            continue
        for handle in handles:
            sources.append(f"{rel} — {handle}")
    status = "ok" if requirements else "NEED_DOCS"
    if not requirements:
        warnings.append("no system analytics; pass --docs. Refusing to invent business cases from code. — NEED_DOCS")
    if not sources:
        sources = ["(none) — no authorized source files"]
    else:
        sources = sorted(set(sources))
    warnings = sorted(set(warnings))
    envelope = {
        "schema_version": "5.0.0",
        "stage": "context-marker",
        "artifacts": {
            "analytics_documentation": {"requirements": requirements},
            "source_code_and_diff": {"sources": sources},
        },
        "warnings": warnings,
    }
    envelope["status"] = status
    return envelope


def scan_documents(doc_entries: list[dict[str, str]], file_limit: int, total_limit: int, id_pattern: str | None = None):
    """Requirement rows of authorized documents in identity order (path, then document order).

    Returns ``(rows, openspec_provenance, warnings, identity)``: ``rows`` are
    ``(path, file digest, text, title)`` tuples that become ``SREQ-NNNN`` in this order,
    ``identity`` maps a row to its heading chain, explicit ID or OpenSpec origin.
    """
    id_re = compile_id_pattern(id_pattern)
    warnings: list[str] = []
    total_docs = 0
    identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    # Rows are appended in document order; that order is the identity order (M29).
    normalized_rows: list[tuple[str, str, str, str]] = []
    openspec_entries: list[dict[str, str]] = []
    openspec_provenance: dict[tuple[str, str, str, str], list[str]] = {}
    for entry in doc_entries:
        rel = entry.get("path")
        digest = entry.get("sha256")
        text = entry.get("content")
        if not isinstance(rel, str) or not isinstance(digest, str) or not isinstance(text, str):
            raise ValueError("docs snapshot row is invalid")
        data = text.encode("utf-8")
        if digest != "sha256:" + hashlib.sha256(data).hexdigest():
            raise ValueError(f"docs snapshot digest does not match content: {rel}")
        if len(data) > file_limit:
            raise RequirementConflict("NEED_DOCS_LIMIT", f"NEED_DOCS_LIMIT: docs file exceeds {file_limit} bytes: {rel}")
        total_docs += len(data)
        if total_docs > total_limit:
            raise RequirementConflict("NEED_DOCS_LIMIT", f"NEED_DOCS_LIMIT: docs snapshot exceeds {total_limit} bytes")
        if is_openspec_document(rel):
            openspec_entries.append(entry)
            continue
        text, redactions = redact_text(text)
        warnings.extend(_redaction_warnings(rel.replace("\\", "/"), redactions))
        rows = extract_inventory(text, id_re)
        for row in rows:
            if row["kind"] not in _REQUIREMENT_KINDS:
                # A bare grouping heading, glossary or table of contents is not a
                # requirement that a test case has to cover (decision 19).
                continue
            snippet = str(row.get("text") or row["title"])
            key = (_compare_key(rel.replace("\\", "/")), digest, snippet, _norm(row["title"]))
            normalized_rows.append(key)
            identity.setdefault(key, {"kind": "markdown", "chain": row["chain"], "explicit_id": row["explicit_id"]})

    openspec_rows, openspec_warnings = _openspec_rows(openspec_entries)
    warnings.extend(openspec_warnings)
    for row in openspec_rows:
        key = (_compare_key(row["path"]), row["digest"], row["text"], row["name"])
        normalized_rows.append(key)
        openspec_provenance[key] = _openspec_marks(row) + row["rename_provenance"]
        identity.setdefault(key, {"kind": "openspec", "path": row["path"], "capability": row["capability"], "name": row["name"],
                                  "rename_provenance": list(row["rename_provenance"])})

    # Snapshot order is not provenance, document order is. Dedupe keeping the first
    # occurrence, then order files by normalized path and keep each file's rows in
    # document order (the sort is stable). A resumed/controller-provided snapshot
    # therefore has the same identity as an equivalent CLI snapshot, and a section
    # appended to a document never renumbers the requirements before it.
    deduped_rows = sorted(dict.fromkeys(normalized_rows), key=lambda row: (row[0], row[1]))
    return deduped_rows, openspec_provenance, warnings, identity


def _project_id_pattern(project: Path) -> str | None:
    """``requirements.id_pattern`` of the project's ``.skillsrc``; None when absent or unreadable."""
    manifest = project / ".skillsrc"
    try:
        if not manifest.is_file():
            return None
        from tools.skillsrc_manifest import load_skillsrc

        value = (load_skillsrc(manifest).get("requirements") or {}).get("id_pattern")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None

def _project_limits(project: Path) -> dict[str, int]:
    """Read optional document limits from the project's ``.skillsrc``; defaults otherwise."""
    manifest = project / ".skillsrc"
    try:
        if not manifest.is_file():
            return context_limits(None)
        from tools.skillsrc_manifest import load_skillsrc

        return context_limits(load_skillsrc(manifest))
    except (OSError, ValueError):
        return context_limits(None)


def main() -> int:
    parser = JsonArgumentParser(description="Build a context-marker envelope from docs and sources.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--docs", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_dir():
        emit_error(f"project not found: {project}")
        return 2
    try:
        envelope = build_context(
            project,
            [Path(item) for item in args.docs],
            [Path(item) for item in args.source],
        )
    except ValueError as error:
        emit_error(str(error))
        return 2
    errors = schema_diagnostics(
        {key: value for key, value in envelope.items() if key != "status"},
        _SCHEMA,
        _ROOT,
    )
    if errors and envelope.get("artifacts", {}).get("analytics_documentation", {}).get("requirements"):
        print(json.dumps({"status": "error", "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0 if envelope.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
