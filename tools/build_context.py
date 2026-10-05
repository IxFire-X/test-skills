"""Build a 5.0.0 context-marker envelope from authorized docs and source handles."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from tools.json_cli import JsonArgumentParser, emit_error
from tools.schema_validation import schema_diagnostics

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "context-marker-output.schema.json"
_DOCS_MAX_FILE = 256 * 1024
_DOCS_MAX_TOTAL = 1024 * 1024

REQ_ID_RE = re.compile(r"\b(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b", re.I)
HEADING_RE = re.compile(r"^(?:#{1,6}\s+[^\n]+|(?:[-*]\s+)?(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b[^\n]*)", re.M | re.I)
API_PATH_RE = re.compile(r"""['"](/api/[^'"]+)['"]""")
FLASK_ROUTE_RE = re.compile(
    r"""@(?:app|router|bp|api)\.(?:get|post|put|patch|delete|route)\(\s*['"]([^'"]+)['"]""",
    re.I,
)


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().strip(".:")


def _compare_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


def extract_inventory(analytics: str) -> list[dict[str, Any]]:
    """Partition authorized requirement prose without dropping unlabelled content.

    Semantic classification belongs to context-marker/reviewer, not heading heuristics.
    """
    text = analytics or ""
    if not text.strip():
        return []
    found: list[dict[str, Any]] = []
    boundaries = sorted({0, len(text), *(match.start() for match in HEADING_RE.finditer(text))})
    for start, stop in zip(boundaries, boundaries[1:]):
        section = text[start:stop].strip()
        if section:
            title = _norm(section.splitlines()[0].lstrip("# "))
            found.append({"kind": "requirement" if REQ_ID_RE.match(title) else "flow", "title": title, "text": section})
    return found


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


_OPENSPEC_PATH = re.compile(r"^openspec/(?:(specs)/(.+)|changes/([^/]+)/specs/(.+))/spec\.md$")
_OPENSPEC_HEADING = re.compile(r"^(##\s+[^\n]+|### Requirement:\s*[^\n]+|#### Scenario:\s*[^\n]+)$", re.M)


def _openspec_rows(entries: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Compose only standard spec documents explicitly present in authorized inputs."""
    baseline: dict[tuple[str, str], dict[str, Any]] = {}
    operations: list[tuple[str, tuple[str, str], dict[str, Any]]] = []
    renames: list[tuple[tuple[str, str], str, str, str]] = []
    warnings: list[str] = []
    changes: set[str] = set()
    for entry in sorted(entries, key=lambda row: row["path"]):
        path = entry["path"].replace("\\", "/")
        if path.startswith("openspec/changes/archive/"):
            warnings.append(f"{path} — {entry['sha256']}; historical archived context, not reapplied to current requirements")
            continue
        match = _OPENSPEC_PATH.fullmatch(path)
        if not match:
            continue
        if entry["sha256"] != "sha256:" + hashlib.sha256(entry["content"].encode("utf-8")).hexdigest():
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"source digest does not match content: {path}")
        is_baseline, capability, change, delta_capability = match.groups()
        if change:
            changes.add(change)
        capability = capability or delta_capability
        text = entry["content"]
        headings = list(_OPENSPEC_HEADING.finditer(text))
        section = "Requirements" if is_baseline else ""
        for index, heading in enumerate(headings):
            title = heading.group().strip()
            if title.startswith("## "):
                section = title[3:].strip()
                if section == "RENAMED Requirements":
                    stop = next((item.start() for item in headings[index + 1:] if item.group().startswith("## ")), len(text))
                    pairs = re.findall(r"^\s*(?:[-*+]\s+)?(FROM|TO):\s*`?### Requirement:\s*(.+?)`?\s*$", text[heading.end():stop], re.M)
                    if not pairs or len(pairs) % 2 or any(pairs[i][0] != "FROM" or pairs[i + 1][0] != "TO" for i in range(0, len(pairs), 2)):
                        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"unpaired rename: {path}")
                    for position in range(0, len(pairs), 2):
                        renames.append(((capability, pairs[position][1]), pairs[position + 1][1], path, entry["sha256"]))
                continue
            if not title.startswith("### Requirement:"):
                continue
            name = title[len("### Requirement:"):].strip()
            stop = next((item.start() for item in headings[index + 1:] if not item.group().startswith("#### Scenario:")), len(text))
            block = text[heading.start():stop].strip()
            scenarios = [(item.group()[len("#### Scenario:"):].strip(), text.count("\n", 0, item.start()) + 1) for item in headings[index + 1:] if heading.start() < item.start() < stop and item.group().startswith("#### Scenario:")]
            line = text.count("\n", 0, heading.start()) + 1
            row = {"capability": capability, "name": name, "text": block, "path": path, "digest": entry["sha256"], "line": line, "scenarios": scenarios, "rename_provenance": []}
            key = (capability, name)
            if is_baseline:
                if section != "Requirements" or key in baseline:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"duplicate or misplaced requirement: {path}:{line} — {name}")
                baseline[key] = row
            else:
                operation = section.removesuffix(" Requirements")
                if operation not in {"ADDED", "MODIFIED", "REMOVED"}:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"requirement has no delta operation: {path}:{line} — {name}")
                operations.append((operation, key, row))
    if len(changes) > 1:
        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", "authorize one selected OpenSpec change, not multiple pending changes")
    for key, new_name, path, digest in renames:
        new_key = (key[0], new_name)
        if key not in baseline or new_key in baseline:
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"rename does not match baseline: {path} — {key[1]} -> {new_name}")
        row = baseline.pop(key)
        row["name"] = new_name
        row["text"] = re.sub(r"^### Requirement:[^\n]+", lambda _: "### Requirement: " + new_name, row["text"], count=1)
        row["rename_provenance"] = [f"{path} — RENAMED Requirement: {key[1]} -> {new_name}", f"{path} — {digest}"]
        baseline[new_key] = row
    seen: set[tuple[str, str]] = set()
    for operation, key, row in operations:
        if key in seen or (operation == "ADDED" and key in baseline) or (operation != "ADDED" and key not in baseline):
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"{operation} does not match baseline: {row['path']}:{row['line']} — {key[1]}")
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
) -> dict[str, Any]:
    project = project.resolve()
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
                data = stream.read(_DOCS_MAX_FILE + 1)
            if len(data) > _DOCS_MAX_FILE:
                raise RequirementConflict("NEED_DOCS_LIMIT", f"docs file exceeds 256KiB: {doc}")
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
        if len(data) > _DOCS_MAX_FILE:
            raise RequirementConflict("NEED_DOCS_LIMIT", f"docs file exceeds 256KiB: {rel}")
        total_docs += len(data)
        if total_docs > _DOCS_MAX_TOTAL:
            raise RequirementConflict("NEED_DOCS_LIMIT", "docs snapshot exceeds 1MiB")
        if _OPENSPEC_PATH.fullmatch(rel.replace("\\", "/")) or rel.replace("\\", "/").startswith("openspec/changes/archive/"):
            openspec_entries.append(entry)
            continue
        rows = extract_inventory(text)
        for row in rows:
            snippet = str(row.get("text") or row["title"])
            normalized_rows.append((_compare_key(rel.replace("\\", "/")), digest, snippet, _norm(row["title"])))

    openspec_rows, openspec_warnings = _openspec_rows(openspec_entries)
    warnings.extend(openspec_warnings)
    for row in openspec_rows:
        key = (_compare_key(row["path"]), row["digest"], row["text"], row["name"])
        normalized_rows.append(key)
        openspec_provenance[key] = _openspec_marks(row) + row["rename_provenance"]

    # Snapshot order is not provenance. Collect/dedupe/sort the complete normalized set
    # before assigning any generated SREQ ID, so a resumed/controller-provided snapshot
    # has exactly the same identity as an equivalent CLI snapshot.
    deduped_rows = sorted(set(normalized_rows), key=lambda row: (row[0], row[1], _compare_key(row[2]), row[2], row[3]))
    for index, (rel, digest, snippet, title) in enumerate(deduped_rows, start=1):
        req_id = _source_requirement_id(index)
        mark = f"{rel} — {_nfc(title)}: {_nfc(snippet)}"
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
