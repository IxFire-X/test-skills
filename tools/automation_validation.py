"""Validation of V5 automation artifacts and bounded static reviews."""

from __future__ import annotations

import json
import hashlib
import keyword
import re
import unicodedata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tools.canonical_document import document_sha256, validate_canonical_document
from tools.schema_validation import classify_version, schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "tc-to-autotest-output.schema.json"
_AUTOTEST_REVIEW_SCHEMA = _ROOT / "schemas" / "autotest-reviewer-output.schema.json"
_JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_JAVA_KEYWORDS = frozenset("""
abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while _
""".split())

_MESSAGES = {
    "AUTOMATION_SOURCE_DOCUMENT": "source document_id must match the canonical document",
    "AUTOMATION_SOURCE_REVISION": "source revision must match the canonical document",
    "AUTOMATION_SOURCE_DIGEST": "source_digest must identify bare canonical document bytes",
    "AUTOMATION_DUPLICATE_FILE": "file_id must be document-global",
    "AUTOMATION_DUPLICATE_PATH": "generated file path must be document-unique",
    "AUTOMATION_MIXED_LANGUAGE": "generated files must use one language",
    "AUTOMATION_PORTABLE_PATH": "path must be a portable relative slash path",
    "AUTOMATION_CONTENT_DIGEST": "content_digest must identify exact UTF-8 generated file bytes",
    "AUTOMATION_UNKNOWN_FILE": "file_id is not declared by generated_files",
    "AUTOMATION_DUPLICATE_SYMBOL": "symbol_id must be unique within its file",
    "AUTOMATION_DUPLICATE_LOCATOR": "a file may not declare the same locator twice",
    "AUTOMATION_INVALID_PYTHON_IDENTIFIER": "Python locator segments must be non-keyword identifiers",
    "AUTOMATION_INVALID_JAVA_IDENTIFIER": "Java locator segments must be non-keyword identifiers",
    "AUTOMATION_LOCATOR_LANGUAGE": "locator kind must match its generated file language",
    "AUTOMATION_UNKNOWN_SYMBOL": "symbol_id is not declared for file_id",
    "AUTOMATION_DUPLICATE_RELATION": "implementation relation key must be unique",
    "AUTOMATION_RELATION_ORDER": "implementation relations must use canonical physical order",
    "AUTOMATION_UNKNOWN_CASE": "case_id is not declared by the canonical document",
    "AUTOMATION_UNKNOWN_STEP": "step_id does not belong to case_id",
    "AUTOMATION_UNKNOWN_EXPECTATION": "expectation_id does not belong to step_id",
    "AUTOMATION_UNKNOWN_ASSERTION": "assertion_id does not belong to expectation_id",
    "AUTOMATION_NONREADY_TARGET": "relations may target only ready steps",
    "AUTOMATION_MISSING_OPERATION_COVERAGE": "ready step requires an operation relation",
    "AUTOMATION_MISSING_ASSERTION_COVERAGE": "ready assertion requires an assertion relation",
    "AUTOMATION_ORPHAN_SYMBOL": "generated symbol must occur in a relation",
    "AUTOMATION_ORPHAN_FILE": "generated file must own a related symbol",
    "AUTOMATION_DUPLICATE_MANUAL_DISPOSITION": "manual disposition must be unique",
    "AUTOMATION_UNKNOWN_MANUAL_STEP": "manual disposition step_id does not belong to case_id",
    "AUTOMATION_MANUAL_DISPOSITION_READY": "manual disposition may target only manual-only steps",
    "AUTOMATION_MANUAL_DISPOSITION_BLOCKED": "manual disposition may not target blocked steps",
    "AUTOMATION_MISSING_MANUAL_DISPOSITION": "manual-only step requires one manual disposition",
    "AUTOMATION_MANUAL_ORDER": "manual dispositions must use canonical case and step order",
    "AUTOMATION_BLOCKER_REQUIRES_BLOCKED": "canonical blockers require BLOCKED automation status",
    "AUTOMATION_BLOCKED_WITHOUT_BLOCKER": "BLOCKED status requires a canonical blocker",
    "AUTOMATION_BLOCKED_CONTENT": "BLOCKED artifacts require diagnostics and four empty coverage arrays",
    "AUTOMATION_GENERATED_DIAGNOSTICS": "GENERATED artifacts require empty diagnostics",
    "AUTOMATION_REVISION_BUDGET": "automation permits an initial version and at most one complete correction",
    "AUTOMATION_REVIEW_BUDGET": "automation permits one static review per version and at most two reviews",
    "AUTOMATION_REVIEW_CADENCE": "each automation version requires exactly one static review",
    "AUTOMATION_CORRECTION_PREDECESSOR": "corrected automation must bind the exact prior automation digest",
    "AUTOMATION_CORRECTION_REVIEW": "corrected automation must bind the exact prior review digest",
    "AUTOMATION_CORRECTION_VERDICT": "only AUTO_FIX_APPLIED may authorize one corrected automation version",
    "AUTOMATION_RUNTIME_FAIL_REGENERATION": "runtime FAIL never authorizes automation regeneration",
    "AUTOTEST_REVIEW_SOURCE": "Review source must match the selected effective document.",
    "AUTOTEST_REVIEW_AUTOMATION_DIGEST": "Review must bind the complete automation artifact.",
    "AUTOTEST_REVIEW_REVISION": "Review revision must match the reviewed automation version.",
    "AUTOTEST_REVIEW_FILE_COVERAGE": "Review must cover declared generated files in physical order.",
    "AUTOTEST_REVIEW_COVERAGE": "Review must cover required symbol pairs in generated-symbol order.",
    "AUTOTEST_REVIEW_RELATION_COVERAGE": "Review must bind the complete implementation relation array.",
    "AUTOTEST_REVIEW_VERDICT": "Only an accepted automation review authorizes execution.",
    "AUTOTEST_REVIEW_ISOLATION": "Accepted review requires verified controller isolation evidence.",
    "AUTOTEST_REVIEW_EFFECTIVE_SELECTION": "Review must bind the same attempt-owned effective canonical selection.",
    "AUTOTEST_REVIEW_SESSION": "Review session and invocation identity must be unique and host-bound.",
}


class AutomationArtifactError(ValueError):
    """Raised by ``required_symbol_pairs`` for an invalid automation artifact."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]) -> None:
        self._diagnostics = tuple(MappingProxyType(dict(item)) for item in diagnostics)
        super().__init__(json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")))

    @property
    def diagnostics(self) -> tuple[Mapping[str, str], ...]:
        return self._diagnostics


def canonical_automation_bytes(artifact: Mapping[str, Any]) -> bytes:
    """Serialize the complete automation artifact without reordering arrays."""
    return json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def automation_sha256(artifact: Mapping[str, Any]) -> str:
    """Return the deterministic identity of the complete automation artifact."""
    return "sha256:" + hashlib.sha256(canonical_automation_bytes(artifact)).hexdigest()


def implementation_relations_sha256(relations: Sequence[Mapping[str, Any]]) -> str:
    """Return the immutable physical-array identity for implementation relations."""
    return "sha256:" + hashlib.sha256(canonical_automation_bytes(list(relations))).hexdigest()


def autotest_review_sha256(review: Mapping[str, Any]) -> str:
    """Return the immutable identity used by a complete corrected automation version."""
    return "sha256:" + hashlib.sha256(canonical_automation_bytes(review)).hexdigest()


def host_isolation_sha256(receipt: Mapping[str, Any]) -> str:
    """Return the controller receipt identity bound into an automation review."""
    return "sha256:" + hashlib.sha256(canonical_automation_bytes(receipt)).hexdigest()


def portable_path_key(path: str) -> str:
    """Return one cross-platform identity for an already slash-normalized path."""
    return unicodedata.normalize("NFC", path).casefold()


def _pointer(*parts: object) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _diagnostic(code: str, path: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": _MESSAGES[code]}


def _portable(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in path):
        return False
    return all(segment and segment not in {".", ".."} and not segment.endswith((".", " ")) for segment in path.split("/"))


def _python_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _java_name(name: str) -> bool:
    return bool(_JAVA_IDENTIFIER.fullmatch(name)) and name not in _JAVA_KEYWORDS


def _document_index(document: Mapping[str, Any]) -> tuple[dict[str, tuple[int, Mapping[str, Any]]], dict[tuple[str, str], tuple[int, Mapping[str, Any]]]]:
    cases: dict[str, tuple[int, Mapping[str, Any]]] = {}
    steps: dict[tuple[str, str], tuple[int, Mapping[str, Any]]] = {}
    for case_index, case in enumerate(document["test_cases"]):
        cases[case["case_id"]] = (case_index, case)
        for step_index, step in enumerate(case["steps"]):
            steps[(case["case_id"], step["step_id"])] = (step_index, step)
    return cases, steps


def validate_automation_artifact(artifact: Any, document: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic V5 schema and atomic-relation diagnostics."""
    canonical = validate_canonical_document(document)
    if canonical:
        return canonical
    version = classify_version(artifact)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [{"path": "/schema_version", "code": version["code"], "message": version["message"]}]
    structural = schema_diagnostics(artifact, _SCHEMA, _ROOT)
    if structural:
        return structural

    artifacts = artifact["artifacts"]
    diagnostics: list[dict[str, str]] = []
    source = artifacts["source"]
    if source["document_id"] != document["document_id"]:
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_DOCUMENT", "/artifacts/source/document_id"))
    if source["revision"] != document["revision"]:
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_REVISION", "/artifacts/source/revision"))
    if source["source_digest"] != document_sha256(document):
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_DIGEST", "/artifacts/source/source_digest"))

    files = artifacts["generated_files"]
    file_map: dict[str, Mapping[str, Any]] = {}
    path_seen: set[str] = set()
    languages: set[str] = set()
    for index, row in enumerate(files):
        file_id, path = row["file_id"], row["path"]
        if file_id in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_FILE", _pointer("artifacts", "generated_files", index, "file_id")))
        else:
            file_map[file_id] = row
        path_key = portable_path_key(path)
        if path_key in path_seen:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_PATH", _pointer("artifacts", "generated_files", index, "path")))
        path_seen.add(path_key)
        if not _portable(path):
            diagnostics.append(_diagnostic("AUTOMATION_PORTABLE_PATH", _pointer("artifacts", "generated_files", index, "path")))
        if row["content_digest"] != "sha256:" + hashlib.sha256(row["content"].encode("utf-8")).hexdigest():
            diagnostics.append(_diagnostic("AUTOMATION_CONTENT_DIGEST", _pointer("artifacts", "generated_files", index, "content_digest")))
        if languages and row["language"] not in languages:
            diagnostics.append(_diagnostic("AUTOMATION_MIXED_LANGUAGE", _pointer("artifacts", "generated_files", index, "language")))
        languages.add(row["language"])

    pair_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    locators: dict[str, set[str]] = {}
    for index, row in enumerate(artifacts["generated_symbols"]):
        file_id, symbol_id = row["file_id"], row["symbol_id"]
        base = _pointer("artifacts", "generated_symbols", index)
        if file_id not in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_FILE", base + "/file_id"))
        pair = (file_id, symbol_id)
        if pair in pair_map:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_SYMBOL", base + "/symbol_id"))
        else:
            pair_map[pair] = row
        locator = row["locator"]
        locator_key = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if locator_key in locators.setdefault(file_id, set()):
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_LOCATOR", base + "/locator"))
        locators[file_id].add(locator_key)
        kind = locator["kind"]
        language = file_map.get(file_id, {}).get("language")
        if (kind.startswith("python_") and language != "python") or (kind == "java_class_method" and language != "java"):
            diagnostics.append(_diagnostic("AUTOMATION_LOCATOR_LANGUAGE", base + "/locator/kind"))
        if kind == "python_module_function" and not _python_name(locator["function_name"]):
            diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/function_name"))
        elif kind == "python_class_method":
            if not all(_python_name(part) for part in locator["qualified_class_name"].split(".")):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/qualified_class_name"))
            if not _python_name(locator["method_name"]):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/method_name"))
        elif kind == "java_class_method":
            if not all(_java_name(part) for part in locator["class_fqn"].split(".")):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_JAVA_IDENTIFIER", base + "/locator/class_fqn"))
            if not _java_name(locator["method_name"]):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_JAVA_IDENTIFIER", base + "/locator/method_name"))

    cases, steps = _document_index(document)
    relations = artifacts["implementation_relations"]
    relation_keys: set[tuple[Any, ...]] = set()
    valid_relation_rows: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
    target_operations: set[tuple[str, str]] = set()
    target_assertions: set[tuple[str, str, str, str]] = set()
    related_pairs: set[tuple[str, str]] = set()
    for index, row in enumerate(relations):
        base = _pointer("artifacts", "implementation_relations", index)
        pair = (row["file_id"], row["symbol_id"])
        row_valid = row["file_id"] in file_map and pair in pair_map
        if row["file_id"] not in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_FILE", base + "/file_id"))
        if pair not in pair_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_SYMBOL", base + "/symbol_id"))
        case = cases.get(row["case_id"])
        if case is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_CASE", base + "/case_id"))
            continue
        step_data = steps.get((row["case_id"], row["step_id"]))
        if step_data is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_STEP", base + "/step_id"))
            continue
        step_index, step = step_data
        if step["manual_only"] or step["automation_blockers"]:
            diagnostics.append(_diagnostic("AUTOMATION_NONREADY_TARGET", base + "/step_id"))
            row_valid = False
        expectation_index = assertion_index = -1
        expectation_id = assertion_id = None
        if row["kind"] == "assertion":
            expectation_id, assertion_id = row["expectation_id"], row["assertion_id"]
            expectation = next(((i, item) for i, item in enumerate(step["expectations"]) if item["expectation_id"] == expectation_id), None)
            if expectation is None:
                diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_EXPECTATION", base + "/expectation_id"))
                continue
            expectation_index, expectation_row = expectation
            assertion = next(((i, item) for i, item in enumerate(expectation_row["assertions"]) if item["assertion_id"] == assertion_id), None)
            if assertion is None:
                diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_ASSERTION", base + "/assertion_id"))
                continue
            assertion_index = assertion[0]
        key = (row["kind"], row["case_id"], row["step_id"], expectation_id, assertion_id, row["file_id"], row["symbol_id"])
        if key in relation_keys:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_RELATION", base))
            row_valid = False
        relation_keys.add(key)
        if not row_valid:
            continue
        if row["kind"] == "assertion":
            target_assertions.add((row["case_id"], row["step_id"], expectation_id, assertion_id))
        else:
            target_operations.add((row["case_id"], row["step_id"]))
        related_pairs.add(pair)
        valid_relation_rows.append(((case[0], step_index, 0 if row["kind"] == "operation" else 1, expectation_index, assertion_index, row["file_id"], row["symbol_id"]), row))
    if [row for _, row in valid_relation_rows] != [row for _, row in sorted(valid_relation_rows, key=lambda item: item[0])]:
        diagnostics.append(_diagnostic("AUTOMATION_RELATION_ORDER", "/artifacts/implementation_relations"))

    for index, row in enumerate(artifacts["generated_symbols"]):
        if (row["file_id"], row["symbol_id"]) not in related_pairs:
            diagnostics.append(_diagnostic("AUTOMATION_ORPHAN_SYMBOL", _pointer("artifacts", "generated_symbols", index)))
    for index, row in enumerate(files):
        if not any(pair[0] == row["file_id"] for pair in related_pairs):
            diagnostics.append(_diagnostic("AUTOMATION_ORPHAN_FILE", _pointer("artifacts", "generated_files", index)))

    manual_rows = artifacts["manual_dispositions"]
    manual_keys: set[tuple[str, str]] = set()
    manual_order: list[tuple[int, int]] = []
    for index, row in enumerate(manual_rows):
        base = _pointer("artifacts", "manual_dispositions", index)
        key = (row["case_id"], row["step_id"])
        if key in manual_keys:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_MANUAL_DISPOSITION", base))
        manual_keys.add(key)
        case, step_data = cases.get(row["case_id"]), steps.get(key)
        if case is None or step_data is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_MANUAL_STEP", base + "/step_id"))
            continue
        step_index, step = step_data
        manual_order.append((case[0], step_index))
        if step["automation_blockers"]:
            diagnostics.append(_diagnostic("AUTOMATION_MANUAL_DISPOSITION_BLOCKED", base))
        elif not step["manual_only"]:
            diagnostics.append(_diagnostic("AUTOMATION_MANUAL_DISPOSITION_READY", base))
    if manual_order != sorted(manual_order):
        diagnostics.append(_diagnostic("AUTOMATION_MANUAL_ORDER", "/artifacts/manual_dispositions"))

    blocked = any(step["automation_blockers"] for _, step in steps.values())
    status = artifacts["automation_status"]
    arrays = ("generated_files", "generated_symbols", "implementation_relations", "manual_dispositions")
    if blocked:
        if status != "BLOCKED":
            diagnostics.append(_diagnostic("AUTOMATION_BLOCKER_REQUIRES_BLOCKED", "/artifacts/automation_status"))
        if not artifacts["diagnostics"] or any(artifacts[name] for name in arrays):
            diagnostics.append(_diagnostic("AUTOMATION_BLOCKED_CONTENT", "/artifacts"))
    elif status == "BLOCKED":
        diagnostics.append(_diagnostic("AUTOMATION_BLOCKED_WITHOUT_BLOCKER", "/artifacts/automation_status"))
    elif artifacts["diagnostics"]:
        diagnostics.append(_diagnostic("AUTOMATION_GENERATED_DIAGNOSTICS", "/artifacts/diagnostics"))

    if status == "GENERATED" and not blocked:
        for (case_id, step_id), (step_index, step) in steps.items():
            case_index = cases[case_id][0]
            step_path = _pointer("test_cases", case_index, "steps", step_index)
            if step["manual_only"]:
                if (case_id, step_id) not in manual_keys:
                    diagnostics.append(_diagnostic("AUTOMATION_MISSING_MANUAL_DISPOSITION", step_path))
                continue
            if step["automation_blockers"]:
                continue
            if (case_id, step_id) not in target_operations:
                diagnostics.append(_diagnostic("AUTOMATION_MISSING_OPERATION_COVERAGE", step_path))
            for expectation_index, expectation in enumerate(step["expectations"]):
                for assertion_index, assertion in enumerate(expectation["assertions"]):
                    if (case_id, step_id, expectation["expectation_id"], assertion["assertion_id"]) not in target_assertions:
                        diagnostics.append(_diagnostic("AUTOMATION_MISSING_ASSERTION_COVERAGE", _pointer("test_cases", case_index, "steps", step_index, "expectations", expectation_index, "assertions", assertion_index)))
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))


def required_symbol_pairs(artifact: Any, document: dict[str, Any]) -> set[tuple[str, str]]:
    """Return required runtime pairs or raise an immutable diagnostic error."""
    diagnostics = validate_automation_artifact(artifact, document)
    if diagnostics:
        raise AutomationArtifactError(diagnostics)
    return {(row["file_id"], row["symbol_id"]) for row in artifact["artifacts"]["implementation_relations"]}


def _review_binding_rows(review: Any, artifact: Any, document: dict[str, Any], host_isolation_receipt: Mapping[str, Any] | None, *, run_root: Path | None = None, attempt_id: str | None = None) -> list[dict[str, str]]:
    """Validate one static review against immutable automation and host evidence."""
    version = classify_version(review)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [{"path": "/schema_version", "code": version["code"], "message": version["message"]}]
    structural = schema_diagnostics(review, _AUTOTEST_REVIEW_SCHEMA, _ROOT)
    if structural:
        return structural
    automation_rows = validate_automation_artifact(artifact, document)
    if automation_rows:
        return automation_rows
    expected_source = dict(artifact["artifacts"]["source"])
    reviewed = review["artifacts"]["autotest_review"]
    expected_files = [
        {"file_id": row["file_id"], "content_digest": row["content_digest"]}
        for row in artifact["artifacts"]["generated_files"]
    ]
    required_pairs = {(row["file_id"], row["symbol_id"]) for row in artifact["artifacts"]["implementation_relations"]}
    expected_pairs = [
        {"file_id": row["file_id"], "symbol_id": row["symbol_id"]}
        for row in artifact["artifacts"]["generated_symbols"]
        if (row["file_id"], row["symbol_id"]) in required_pairs
    ]
    rows: list[dict[str, str]] = []
    if reviewed["source"] != expected_source:
        rows.append(_diagnostic("AUTOTEST_REVIEW_SOURCE", "/artifacts/autotest_review/source"))
    if reviewed["automation_revision"] != artifact["artifacts"]["automation_revision"]:
        rows.append(_diagnostic("AUTOTEST_REVIEW_REVISION", "/artifacts/autotest_review/automation_revision"))
    if reviewed["automation_sha256"] != automation_sha256(artifact):
        rows.append(_diagnostic("AUTOTEST_REVIEW_AUTOMATION_DIGEST", "/artifacts/autotest_review/automation_sha256"))
    if reviewed["reviewed_files"] != expected_files:
        rows.append(_diagnostic("AUTOTEST_REVIEW_FILE_COVERAGE", "/artifacts/autotest_review/reviewed_files"))
    if reviewed["reviewed_symbol_pairs"] != expected_pairs:
        rows.append(_diagnostic("AUTOTEST_REVIEW_COVERAGE", "/artifacts/autotest_review/reviewed_symbol_pairs"))
    if reviewed["reviewed_relations_sha256"] != implementation_relations_sha256(artifact["artifacts"]["implementation_relations"]):
        rows.append(_diagnostic("AUTOTEST_REVIEW_RELATION_COVERAGE", "/artifacts/autotest_review/reviewed_relations_sha256"))
    receipt = host_isolation_receipt
    boundary = None
    state = None
    if run_root is not None and isinstance(attempt_id, str):
        try:
            from tools.pilot_state import _read_attempt_receipt_with_state, _run_root, derive_state

            project, root = _run_root(run_root)
            state = derive_state(root)
            kind = f"automation-review-boundary-r{reviewed['automation_revision']}"
            boundary = _read_attempt_receipt_with_state(
                project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK",
            )["record"]
        except (ValueError, KeyError, TypeError):
            boundary = None
    expected = {
        "automation_digest": automation_sha256(artifact), "automation_revision": reviewed["automation_revision"],
        "reviewer_session_id": reviewed["reviewer_session_id"], "generator_invocation_id": reviewed["generator_invocation_id"],
        "reviewer_invocation_id": reviewed["reviewer_invocation_id"], "role_policy": "autotest-static-reviewer-v1",
    }
    if (
        not isinstance(boundary, Mapping)
        or not isinstance(receipt, Mapping)
        or dict(receipt) != dict(boundary)
        or any(boundary.get(key) != value for key, value in expected.items())
        or boundary.get("host_isolation", {}).get("fresh_context") is not True
        or boundary.get("host_isolation", {}).get("distinct_invocations") is not True
        or reviewed["host_isolation_sha256"] != boundary.get("digest")
        or boundary.get("effective_canonical_digest") != document_sha256(document)
        or boundary.get("effective_bundle_receipt_digest") != expected_source.get("effective_bundle_receipt_digest")
    ):
        rows.append(_diagnostic("AUTOTEST_REVIEW_ISOLATION", "/artifacts/autotest_review/host_isolation_sha256"))
    if run_root is not None and isinstance(attempt_id, str):
        try:
            from tools.pilot_state import _read_effective_canonical_with_state

            if state is None:
                raise ValueError("durable review state is unavailable")
            effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
        except (KeyError, TypeError, ValueError):
            effective = None
        if (
            not isinstance(effective, Mapping)
            or effective.get("document") != document
            or effective.get("document_digest") != document_sha256(document)
            or effective.get("effective_bundle_receipt_digest") != expected_source.get("effective_bundle_receipt_digest")
        ):
            rows.append(_diagnostic("AUTOTEST_REVIEW_EFFECTIVE_SELECTION", "/artifacts/autotest_review/source/effective_bundle_receipt_digest"))
    return sorted(rows, key=lambda row: (row["path"], row["code"], row["message"]))


def validate_accepted_autotest_review(review: Any, artifact: Any, document: dict[str, Any], *, host_isolation_receipt: Mapping[str, Any] | None = None, run_root: Path | None = None, attempt_id: str | None = None) -> list[dict[str, str]]:
    """Prove that one accepted static review covers exact automation and host evidence."""
    rows = _review_binding_rows(review, artifact, document, host_isolation_receipt, run_root=run_root, attempt_id=attempt_id)
    if rows:
        return rows
    if review["artifacts"]["autotest_review"]["verdict"] != "ПРИНЯТО":
        return [_diagnostic("AUTOTEST_REVIEW_VERDICT", "/artifacts/autotest_review/verdict")]
    return []


def validate_automation_revision_chain(versions: Sequence[Any], reviews: Sequence[Any], document: dict[str, Any], *, host_isolation_receipts: Sequence[Mapping[str, Any]] = (), run_root: Path | None = None, attempt_id: str | None = None, runtime_verdict: str | None = None) -> list[dict[str, str]]:
    """Validate the initial automation and its single, fully bound correction budget."""
    rows: list[dict[str, str]] = []
    if len(versions) > 2:
        rows.append(_diagnostic("AUTOMATION_REVISION_BUDGET", "/versions"))
    if len(reviews) > 2:
        rows.append(_diagnostic("AUTOMATION_REVIEW_BUDGET", "/reviews"))
    if len(versions) != len(reviews) or len(reviews) != len(host_isolation_receipts):
        rows.append(_diagnostic("AUTOMATION_REVIEW_CADENCE", "/reviews"))
    usable = min(len(versions), len(reviews), len(host_isolation_receipts), 2)
    session_ids: set[str] = set()
    reviewer_invocations: set[str] = set()
    for index in range(usable):
        artifact, review, receipt = versions[index], reviews[index], host_isolation_receipts[index]
        artifact_rows = validate_automation_artifact(artifact, document)
        rows.extend(artifact_rows)
        review_rows = _review_binding_rows(review, artifact, document, receipt, run_root=run_root, attempt_id=attempt_id)
        rows.extend(review_rows)
        if not isinstance(artifact, Mapping) or not isinstance(review, Mapping) or not isinstance(artifact.get("artifacts"), Mapping) or not isinstance(review.get("artifacts"), Mapping) or not isinstance(review["artifacts"].get("autotest_review"), Mapping):
            continue
        generated = artifact["artifacts"]
        reviewed = review["artifacts"]["autotest_review"]
        if "automation_revision" not in generated or "reviewer_session_id" not in reviewed or "reviewer_invocation_id" not in reviewed:
            continue
        if generated["automation_revision"] != index + 1:
            rows.append(_diagnostic("AUTOMATION_REVISION_BUDGET", _pointer("versions", index, "artifacts", "automation_revision")))
        if reviewed["reviewer_session_id"] in session_ids or reviewed["reviewer_invocation_id"] in reviewer_invocations:
            rows.append(_diagnostic("AUTOTEST_REVIEW_SESSION", _pointer("reviews", index, "artifacts", "autotest_review", "reviewer_session_id")))
        session_ids.add(reviewed["reviewer_session_id"])
        reviewer_invocations.add(reviewed["reviewer_invocation_id"])
        if index == 1:
            previous, previous_review = versions[0], reviews[0]
            if not isinstance(previous, Mapping) or not isinstance(previous_review, Mapping) or not isinstance(previous_review.get("artifacts"), Mapping) or not isinstance(previous_review["artifacts"].get("autotest_review"), Mapping) or "predecessor_automation_sha256" not in generated or "correction_review_sha256" not in generated:
                continue
            if generated["predecessor_automation_sha256"] != automation_sha256(previous):
                rows.append(_diagnostic("AUTOMATION_CORRECTION_PREDECESSOR", _pointer("versions", index, "artifacts", "predecessor_automation_sha256")))
            if generated["correction_review_sha256"] != autotest_review_sha256(previous_review):
                rows.append(_diagnostic("AUTOMATION_CORRECTION_REVIEW", _pointer("versions", index, "artifacts", "correction_review_sha256")))
            if previous_review["artifacts"]["autotest_review"]["verdict"] != "AUTO_FIX_APPLIED":
                rows.append(_diagnostic("AUTOMATION_CORRECTION_VERDICT", _pointer("reviews", 0, "artifacts", "autotest_review", "verdict")))
    return sorted(rows, key=lambda row: (row["path"], row["code"], row["message"]))
