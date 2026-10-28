"""FaT-CV: the reviewer's AUTO_FIX as a list of exact edits to human fields; code applies them, never rewrites them.

A ``successor_edits`` item is ``{correction_id, case_id, field, step_id?, expectation_id?, index?, old, new}``.  ``field``
is one of ``EDITABLE``: ``title``/``objective`` of a case, ``precondition`` (``index``), ``action``/``test_data`` of a step
(``step_id``), ``expectation`` text (``step_id`` and ``expectation_id``).  An edit applies only when ``old`` equals the
current text exactly; ``new`` is placed as is.  The successor is the candidate with the edits, ``revision`` 2 and
``parent_sha256`` of the candidate; the usual canonical and successor checks then run on it unchanged.

``python -m tools.review_edits --candidate <r1.json> --edits <edits.json>`` only reads: it prints the ``effective``
reference the reviewer puts into the report (the digest is never computed by the model) or the diagnostics, exit 1.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.json_cli import JsonArgumentParser

EDITABLE = ("title", "objective", "precondition", "action", "test_data", "expectation")
_CASE_FIELDS = {"title", "objective"}
_STEP_FIELDS = {"action", "test_data"}


def _diagnostic(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _target(document: dict[str, Any], edit: Mapping[str, Any]) -> tuple[Any, Any, tuple[Any, ...]] | None:
    """``(container, key, identity)`` of the edited text, or ``None`` when the target is unknown."""
    field = edit.get("field")
    case = next((item for item in document["test_cases"] if item["case_id"] == edit.get("case_id")), None)
    if case is None:
        return None
    if field in _CASE_FIELDS:
        return (case, field, (case["case_id"], field)) if not {"step_id", "expectation_id", "index"} & set(edit) else None
    if field == "precondition":
        index = edit.get("index")
        if type(index) is not int or not 0 <= index < len(case["preconditions"]) or {"step_id", "expectation_id"} & set(edit):
            return None
        return case["preconditions"], index, (case["case_id"], field, index)
    step = next((item for item in case["steps"] if item["step_id"] == edit.get("step_id")), None)
    if step is None or "index" in edit:
        return None
    if field in _STEP_FIELDS:
        return (step, field, (case["case_id"], step["step_id"], field)) if "expectation_id" not in edit else None
    expectation = next((item for item in step["expectations"] if item["expectation_id"] == edit.get("expectation_id")), None)
    if expectation is None:
        return None
    return expectation, "text", (case["case_id"], step["step_id"], expectation["expectation_id"])


def _apply(document: dict[str, Any], edits: Sequence[Any], correction_ids: set[str] | None, *, reverse: bool = False) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    seen: set[tuple[Any, ...]] = set()
    for position, edit in enumerate(edits):
        path = f"/successor_edits/{position}"
        if not isinstance(edit, Mapping) or edit.get("field") not in EDITABLE:
            diagnostics.append(_diagnostic(path, "EDIT_FIELD_FORBIDDEN", f"only human fields may be edited: {', '.join(EDITABLE)}"))
            continue
        old, new = (edit.get("new"), edit.get("old")) if reverse else (edit.get("old"), edit.get("new"))
        if not isinstance(old, str) or not isinstance(new, str) or not new.strip() or old == new:
            diagnostics.append(_diagnostic(path, "EDIT_TEXT", "old and new are different texts and new is not empty"))
            continue
        if correction_ids is not None and edit.get("correction_id") not in correction_ids:
            diagnostics.append(_diagnostic(path, "EDIT_CORRECTION_UNKNOWN", "correction_id must name a correction of the report"))
            continue
        target = _target(document, edit)
        if target is None:
            diagnostics.append(_diagnostic(path, "EDIT_TARGET_UNKNOWN", "case, step, expectation or precondition index is unknown"))
            continue
        container, key, identity = target
        if identity in seen:
            diagnostics.append(_diagnostic(path, "EDIT_DUPLICATE", "one text may be edited once"))
            continue
        seen.add(identity)
        if container[key] != old:
            diagnostics.append(_diagnostic(path, "EDIT_OLD_MISMATCH", "old must equal the current text exactly"))
            continue
        container[key] = new
    return diagnostics


def apply_edits(candidate: Mapping[str, Any], edits: Sequence[Any], correction_ids: set[str] | None = None) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """``(successor, [])`` or ``(None, diagnostics)``; the candidate itself is never changed."""
    from tools.canonical_document import document_sha256

    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)) or not edits:
        return None, [_diagnostic("/successor_edits", "EDIT_LIST", "successor_edits is a non-empty list")]
    if candidate.get("revision") != 1 or candidate.get("parent_sha256") is not None:
        return None, [_diagnostic("/revision", "EDIT_CANDIDATE", "edits apply to an assembled revision 1 without a parent")]
    successor = copy.deepcopy(dict(candidate))
    diagnostics = _apply(successor, edits, correction_ids)
    if diagnostics:
        return None, diagnostics
    successor["revision"] = candidate["revision"] + 1
    successor["parent_sha256"] = document_sha256(dict(candidate))
    return successor, []


def edits_rebuild(document: Mapping[str, Any], edits: Sequence[Any], candidate_digest: str) -> bool:
    """Whether ``document`` is exactly the candidate of ``candidate_digest`` with ``edits`` (for checks without the candidate)."""
    from tools.canonical_document import document_sha256

    try:
        candidate = copy.deepcopy(dict(document))
        if _apply(candidate, edits, None, reverse=True):
            return False
        candidate["revision"], candidate["parent_sha256"] = 1, None
        if document_sha256(candidate) != candidate_digest:
            return False
        successor, diagnostics = apply_edits(candidate, edits)
        return not diagnostics and successor == dict(document)
    except (KeyError, TypeError, ValueError):
        return False


def reviewer_successor(candidate: Mapping[str, Any], artifacts: Mapping[str, Any]) -> dict[str, Any] | None:
    """The successor of a reviewer output: ``successor_document`` as is, or the candidate with ``successor_edits``.

    Raises ``revision_selection.SelectionError`` when the edits do not apply.
    """
    from tools.revision_selection import SelectionError

    if "successor_edits" not in artifacts:
        successor = artifacts.get("successor_document")
        return dict(successor) if isinstance(successor, Mapping) else None
    report = artifacts.get("validation_report")
    corrections = report.get("corrections") if isinstance(report, Mapping) else None
    ids = {str(item.get("id")) for item in corrections or [] if isinstance(item, Mapping)}
    successor, diagnostics = apply_edits(candidate, artifacts["successor_edits"], ids)
    if diagnostics:
        raise SelectionError(diagnostics)
    return successor


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="Apply reviewer edits to a candidate and print the effective reference.")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--edits", required=True, help="a JSON list of edits, or a reviewer output with artifacts.successor_edits")
    args = parser.parse_args(argv)
    try:
        from tools.canonical_document import CanonicalDocumentError, document_sha256, require_valid_canonical_document

        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        value = json.loads(Path(args.edits).read_text(encoding="utf-8"))
        edits = value["artifacts"]["successor_edits"] if isinstance(value, Mapping) else value
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 2
    successor, diagnostics = apply_edits(candidate, edits)
    if successor is not None:
        try:
            require_valid_canonical_document(successor)
        except CanonicalDocumentError as error:
            diagnostics = list(error.diagnostics)
        if not diagnostics:
            from tools.revision_selection import validate_successor

            diagnostics = validate_successor(candidate, successor)
    if diagnostics:
        print(json.dumps({"status": "invalid", "diagnostics": diagnostics[:20]}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "edits": len(edits), "effective": {
        "document_id": successor["document_id"], "revision": successor["revision"], "document_sha256": document_sha256(successor)}},
        ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
