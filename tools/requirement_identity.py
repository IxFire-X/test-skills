"""Requirement keys and text digests (wave 3, I).

A key names a requirement independently of its ``SREQ-NNNN`` number, which shifts when a
section is inserted before it:

* OpenSpec — ``openspec:<spec root/>capability#requirement name``; a ``RENAMED Requirements``
  pair keeps the old key in ``renamed_from``;
* Markdown — ``md:<path>#<ID>`` for a section that starts with an explicit ID (built-in
  ``REQ-``/``AC-``… or ``.skillsrc`` ``requirements.id_pattern``), otherwise
  ``md:<path>#<heading chain>``; a repeated chain in one file gets ``~2``, ``~3``….

``text_digest`` is the SHA-256 of the requirement text without its heading line or leading
ID, NFC-normalized with collapsed whitespace: renaming a heading keeps it, editing the text
changes it.  Rows come from the same scan as ``build_context`` (``scan_documents``), so the
``source_requirement_id`` of a row is the SREQ the context envelope gives the same text.
The keys are stored in the suite manifest, not in the closed context or canonical schemas.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from tools.build_context import (
    _BUILTIN_ID,
    _OPENSPEC_PATH,
    _OPENSPEC_REQUIREMENT_LINE,
    _source_requirement_id,
    compile_id_pattern,
    scan_documents,
)
from tools.project_inventory import DEFAULT_CONTEXT_LIMITS

_RENAME = re.compile(r" — RENAMED Requirement: (.+) -> (.+)$")


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalized_text(text).encode("utf-8")).hexdigest()


def requirement_body(text: str, explicit_id: str | None = None) -> str:
    """The requirement text without its Markdown heading or OpenSpec requirement line and leading ID."""
    lines = (text or "").split("\n")
    if lines and (re.match(r"^\s{0,3}#{1,6}\s", lines[0]) or _OPENSPEC_REQUIREMENT_LINE.match(lines[0])):
        heading = re.sub(r"^\s{0,3}#{1,6}\s*", "", lines[0])
        rest = "\n".join(lines[1:])
        # A heading that is itself the ID line ("### REQ-7 Login") keeps its words after the ID.
        if explicit_id and heading.lstrip("-*+ ").lstrip("*").startswith(explicit_id):
            return heading.split(explicit_id, 1)[1].lstrip(" .:)-") + "\n" + rest
        return rest
    if explicit_id:
        head = re.sub(r"^(?:[-*+]\s+)?(?:\*\*)?", "", text or "")
        if head.startswith(explicit_id):
            return head[len(explicit_id):].lstrip(" .:)*-")
    return text or ""


def _openspec_key(origin: Mapping[str, Any], name: str | None = None) -> str:
    match = _OPENSPEC_PATH.fullmatch(origin["path"])
    root = (match.group(1) or "") if match else ""
    return f"openspec:{root + '/' if root else ''}{origin['capability']}#{name or origin['name']}"


def identify(doc_entries: Sequence[Mapping[str, str]], id_pattern: str | None = None, *,
             file_limit: int | None = None, total_limit: int | None = None) -> list[dict[str, Any]]:
    """Keyed rows in SREQ order: ``source_requirement_id``, ``key``, ``path``, ``title``, ``text``, ``text_digest``…"""
    compile_id_pattern(id_pattern)
    rows, _provenance, _warnings, identity = scan_documents(
        [dict(entry) for entry in doc_entries],
        file_limit if file_limit is not None else DEFAULT_CONTEXT_LIMITS["docs_file_bytes"],
        total_limit if total_limit is not None else DEFAULT_CONTEXT_LIMITS["docs_total_bytes"],
        id_pattern,
    )
    keyed: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        path, file_digest, text, title = row
        origin = identity[row]
        renamed_from: list[str] = []
        if origin["kind"] == "openspec":
            key = _openspec_key(origin)
            for mark in origin["rename_provenance"]:
                found = _RENAME.search(mark)
                if found:
                    renamed_from.append(_openspec_key(origin, found.group(1).strip()))
            body = requirement_body(text)
            explicit = None
        else:
            explicit = origin["explicit_id"]
            key = f"md:{path}#{explicit}" if explicit else f"md:{path}#{' > '.join(origin['chain'])}"
            body = requirement_body(text, explicit)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}~{seen[key]}"
        keyed.append({"source_requirement_id": _source_requirement_id(index), "key": key, "path": path, "title": title,
                      "explicit_id": explicit, "text": text, "text_digest": text_digest(body), "file_digest": file_digest,
                      "renamed_from": renamed_from})
    return keyed


def key_path(key: str) -> str:
    """The file (Markdown) or capability (OpenSpec) part of a key."""
    return key.split("#", 1)[0]


def rename_pairs(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """``(old key, new key)`` of renamed requirements.

    An OpenSpec rename is explicit (``renamed_from``).  Otherwise a key that disappeared and a
    key that appeared in the same file with the same text digest are one requirement under a new
    heading or ID — only when that digest is unique on both sides.
    """
    before = list(before)
    after = list(after)
    old_keys = {row["key"] for row in before}
    new_keys = {row["key"] for row in after}
    pairs: set[tuple[str, str]] = set()
    for row in after:
        for old in row.get("renamed_from") or []:
            if old in old_keys and old not in new_keys and row["key"] not in old_keys:
                pairs.add((old, row["key"]))
    paired_old = {old for old, _new in pairs}
    paired_new = {new for _old, new in pairs}
    removed = [row for row in before if row["key"] not in new_keys and row["key"] not in paired_old]
    added = [row for row in after if row["key"] not in old_keys and row["key"] not in paired_new]

    def by_digest(rows: list[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            grouped.setdefault((key_path(row["key"]), row["text_digest"]), []).append(row)
        return grouped

    gone, new = by_digest(removed), by_digest(added)
    for marker, rows in gone.items():
        if len(rows) == 1 and len(new.get(marker, [])) == 1:
            pairs.add((rows[0]["key"], new[marker][0]["key"]))
    return sorted(pairs)


def identities_from_record(source_requirements: Sequence[Mapping[str, Any]], id_pattern: str | None = None) -> list[dict[str, Any]]:
    """Keys from the run's own record when its documents changed since (suite migration).

    Without the document the heading chain is unknown, so a Markdown key uses the section title
    (``md:<path>#<title>``) unless the title starts with an explicit ID; an OpenSpec row keeps its
    capability and name from the provenance.  A later scan pairs such a key with the full one by
    the text digest (``rename_pairs``), so nothing looks removed.
    """
    from tools.build_context import explicit_requirement_id

    id_re = compile_id_pattern(id_pattern)
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for item in source_requirements:
        provenance = [str(mark) for mark in item.get("provenance") or []]
        path, _sep, title = (provenance[0] if provenance else " — ").partition(" — ")
        spec = next((mark for mark in provenance if " — capability=" in mark and "; ### Requirement: " in mark and "; #### Scenario:" not in mark), None)
        if spec is not None:
            capability = spec.split(" — capability=", 1)[1].split(";", 1)[0]
            name = spec.split("; ### Requirement: ", 1)[1]
            match = _OPENSPEC_PATH.fullmatch(path.split(":", 1)[0])
            root = (match.group(1) or "") if match else ""
            key = f"openspec:{root + '/' if root else ''}{capability}#{name}"
            explicit = None
            body = requirement_body(str(item.get("text") or ""))
        else:
            explicit = explicit_requirement_id(title, id_re)
            key = f"md:{path}#{explicit or title}"
            body = requirement_body(str(item.get("text") or ""), explicit)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}~{seen[key]}"
        rows.append({"source_requirement_id": item["source_requirement_id"], "key": key, "path": path.split(":", 1)[0], "title": title,
                     "explicit_id": explicit, "text": str(item.get("text") or ""), "text_digest": text_digest(body),
                     "file_digest": str(item.get("digest") or ""), "renamed_from": []})
    return rows
