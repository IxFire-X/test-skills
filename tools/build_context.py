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
        rows = extract_inventory(text)
        for row in rows:
            snippet = str(row.get("text") or row["title"])
            normalized_rows.append((_compare_key(rel.replace("\\", "/")), digest, snippet, _norm(row["title"])))

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
            "provenance": [mark, digest_mark],
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
