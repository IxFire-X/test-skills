#!/usr/bin/env python3
"""Safely compile discovery evidence into a portable .skillsrc manifest."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping

import yaml

if __package__:
    from .discover_project import discover_project
    from .skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc
else:
    from discover_project import discover_project
    from skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc


class InitError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NeedsInput(InitError):
    def __init__(self, questions: list[dict[str, Any]]):
        super().__init__("needs_input", "answers are required")
        self.questions = questions


class Conflict(InitError):
    def __init__(self, questions: list[dict[str, Any]]):
        super().__init__("conflict", "existing manifest requires an explicit decision")
        self.questions = questions


def _set_field(module: dict[str, Any], field: str, value: object) -> None:
    parts = field.split(".")
    if len(parts) < 4 or parts[0] != "modules":
        raise InitError("answer_unknown", "unknown question field")
    target: dict[str, Any] = module
    for part in parts[2:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def compile_skillsrc(discovery: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    questions = discovery.get("questions", [])
    if not isinstance(questions, list):
        raise InitError("discovery_invalid", "discovery questions are invalid")
    unresolved: list[dict[str, Any]] = []
    known: set[str] = set()
    selected: list[tuple[str, object]] = []
    for question in questions:
        if not isinstance(question, Mapping) or not isinstance(question.get("id"), str):
            raise InitError("discovery_invalid", "discovery question is invalid")
        question_id = question["id"]
        known.add(question_id)
        options = {item.get("id"): item for item in question.get("options", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
        answer = answers.get(question_id)
        if answer is None:
            unresolved.append(dict(question)); continue
        if answer not in options:
            raise InitError("answer_unknown", f"unknown option for {question_id}")
        selected.append((str(question.get("field")), options[answer].get("value")))
    deferred = {key for key in answers if key not in known}
    if any(not key.startswith(("replace:", "remove:", "migrate-")) for key in deferred):
        raise InitError("answer_unknown", "answer does not match a current discovery question")
    if unresolved and deferred:
        raise InitError("answer_unknown", "reconciliation answer supplied before discovery is resolved")
    if unresolved:
        raise NeedsInput(unresolved)
    modules = copy.deepcopy(discovery.get("modules", []))
    if not isinstance(modules, list) or not modules:
        raise InitError("discovery_invalid", "discovery has no modules")
    by_id = {module.get("id"): module for module in modules if isinstance(module, dict)}
    for field, value in selected:
        parts = field.split(".")
        if len(parts) < 4 or parts[1] not in by_id:
            raise InitError("discovery_invalid", "question does not refer to a discovered module")
        _set_field(by_id[parts[1]], field, value)
    return {"version": "3.0", "project": {"name": discovery.get("project_name", "project")}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": modules}


def _question(question_id: str, field: str, operation: str) -> dict[str, Any]:
    """Public reconciliation question: deliberately contains no manifest values."""
    return {
        "id": question_id,
        "field": field,
        "impact": "An existing manifest value would change",
        "options": [
            {"id": "keep-existing", "value": "keep-existing", "evidence": []},
            {"id": "use-detected", "value": "use-detected", "evidence": []},
        ],
        "operation": operation,
    }


def _merge_additive(existing: Any, proposed: Any, field: str, questions: list[dict[str, Any]], path: list[str]) -> Any:
    if isinstance(existing, dict) and isinstance(proposed, dict):
        result = copy.deepcopy(existing)
        for key, value in proposed.items():
            result[key] = _merge_additive(result[key], value, f"{field}.{key}", questions, path + [key]) if key in result else copy.deepcopy(value)
        return result
    if existing == proposed or field.endswith(".detected_from"):
        return copy.deepcopy(existing if existing != proposed else proposed)
    question = _question(f"replace:{field}", field, "replace")
    question["_path"] = path
    question["_detected"] = copy.deepcopy(proposed)
    questions.append(question)
    return copy.deepcopy(existing)


def reconcile_skillsrc(existing: dict[str, Any] | None, proposed: dict[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    if existing is None:
        if answers:
            raise InitError("answer_unknown", "answer does not match a current reconciliation question")
        return {"status": "created", "document": proposed, "questions": []}
    normalized = normalize_skillsrc(existing)
    existing_modules = normalized["modules"]
    proposed_modules = proposed["modules"]
    if existing.get("version") != "3.0":
        if len(proposed_modules) == 1 and _v2_matches_detected(existing_modules[0], proposed_modules[0]):
            return {"status": "unchanged", "document": existing, "questions": []}
        unsupported = _v2_unsupported_fields(existing)
        if unsupported:
            question = _question("migrate-v2-preservation", "version", "replace")
            question["impact"] = "This v2 manifest contains fields without a lossless v3 representation"
            return {"status": "conflict", "document": existing, "questions": [question]}
        migration = _question("migrate-v2-to-v3", "version", "replace")
        if set(answers) - {migration["id"]}:
            raise InitError("answer_unknown", "answer does not match a current reconciliation question")
        answer = answers.get(migration["id"])
        if answer is None:
            return {"status": "conflict", "document": existing, "questions": [migration]}
        if answer not in {"keep-existing", "use-detected"}:
            raise InitError("answer_unknown", "unknown migration option")
        if answer == "keep-existing":
            return {"status": "unchanged", "document": existing, "questions": []}
        migrated = copy.deepcopy(proposed)
        migrated["project"]["name"] = existing["project"]["name"]
        if "methodology" in existing:
            migrated["project"]["methodology"] = copy.deepcopy(existing["methodology"])
        for key in ("resolution", "contracts", "skills_registry"):
            if key in existing:
                migrated[key] = copy.deepcopy(existing[key])
        return {"status": "updated", "document": migrated, "questions": []}
    known = {module["id"]: module for module in existing_modules}
    merged = copy.deepcopy(existing)
    questions: list[dict[str, Any]] = []
    output = []
    for module in proposed_modules:
        old = known.pop(module["id"], None)
        output.append(copy.deepcopy(module) if old is None else _merge_additive(old, module, f"modules.{module['id']}", questions, ["modules", module["id"]]))
    if known:
        for module_id, module in known.items():
            question = _question(f"remove:modules.{module_id}", f"modules.{module_id}", "remove")
            question["_module_id"] = module_id
            questions.append(question)
            output.append(copy.deepcopy(module))
    if questions:
        if set(answers) - {question["id"] for question in questions}:
            raise InitError("answer_unknown", "answer does not match a current reconciliation question")
        unanswered = [q for q in questions if q["id"] not in answers]
        if unanswered:
            return {"status": "conflict", "document": existing, "questions": [_public_question(q) for q in questions]}
        for question in questions:
            if answers[question["id"]] not in {"keep-existing", "use-detected"}:
                raise InitError("answer_unknown", f"unknown option for {question['id']}")
            if answers[question["id"]] == "use-detected":
                if question["operation"] == "remove":
                    output = [item for item in output if item["id"] != question["_module_id"]]
                else:
                    _set_document_path(output, question["_path"], question["_detected"])
    elif answers:
        raise InitError("answer_unknown", "answer does not match a current reconciliation question")
    merged["modules"] = output
    return {"status": "unchanged" if merged == existing else "updated", "document": merged, "questions": []}


def _public_question(question: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in question.items() if not key.startswith("_")}


def _set_document_path(modules: list[dict[str, Any]], path: list[str], value: Any) -> None:
    module = next(item for item in modules if item["id"] == path[1])
    target: dict[str, Any] = module
    for part in path[2:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(value)


def _v2_matches_detected(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """V2 has no module envelope; retain it when its core stack agrees."""
    if old.get("id") != "root" or new.get("id") != "root" or old.get("root") != new.get("root"):
        return False
    for group in ("stack", "test", "paths", "feature_sources"):
        old_group = old.get(group, {})
        new_group = new.get(group, {})
        if not isinstance(old_group, Mapping) or not isinstance(new_group, Mapping):
            return False
        if any(new_group.get(key) != value for key, value in old_group.items()):
            return False
    return True


def _v2_unsupported_fields(document: Mapping[str, Any]) -> list[str]:
    unsupported: list[str] = []
    if "type" in document.get("project", {}):
        unsupported.append("project.type")
    for group, keys in (("paths", ("docs",)), ("test", ("api_client", "database")), ("sdd", ("asyncapi", "domain"))):
        for key in keys:
            if key in document.get(group, {}):
                unsupported.append(f"{group}.{key}")
    return unsupported


def _payload(document: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False, default_flow_style=False).replace("\r\n", "\n").encode("utf-8")


def atomic_write_skillsrc(project_dir: Path, destination: Path, document: Mapping[str, Any], expected_fingerprint: str, expected_destination: bytes | None) -> None:
    handle, temporary_name = tempfile.mkstemp(prefix=".skillsrc.", suffix=".tmp", dir=project_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_payload(document))
            stream.flush()
            os.fsync(stream.fileno())
        load_skillsrc(temporary)
        fresh = discover_project(project_dir)
        if fresh.get("fingerprint") != expected_fingerprint:
            raise InitError("project_changed", "project manifests changed during initialization")
        current = destination.read_bytes() if destination.exists() else None
        if current != expected_destination:
            raise InitError("destination_changed", ".skillsrc changed during initialization")
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()


def _receipt(status: str, root: Path, written: bool, modules: list[dict[str, Any]], questions: list[dict[str, Any]], errors: list[str], fingerprint: str) -> dict[str, Any]:
    operation = {"created": "create", "updated": "update"}.get(status)
    receipt = {
        "status": status,
        "skillsrc_path": ".skillsrc",
        "written": written,
        "module_ids": [module["id"] for module in modules],
        "questions": questions,
        "changes": [] if operation is None else [{"operation": operation, "path": ".skillsrc"}],
        "warnings": [],
        "errors": errors,
        "discovery_fingerprint": fingerprint,
    }
    return _sanitize(receipt)


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@", r"\1[redacted]@", value)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    return value


def ensure_skillsrc(project_dir: Path, answers: Mapping[str, str], write: bool) -> dict[str, Any]:
    root = project_dir.resolve(); destination = root / ".skillsrc"; discovery = discover_project(root)
    fingerprint = str(discovery.get("fingerprint", hashlib.sha256(b"").hexdigest()))
    modules = list(discovery.get("modules", []))
    if discovery.get("status") == "error": return _receipt("error", root, False, modules, [], list(discovery.get("errors", [])), fingerprint)
    try:
        proposed = compile_skillsrc(discovery, answers)
    except NeedsInput as error:
        return _receipt("needs_input", root, False, modules, error.questions, [], fingerprint)
    except InitError as error:
        return _receipt("error", root, False, modules, [], [error.code], fingerprint)
    original = destination.read_bytes() if destination.exists() else None
    try:
        existing = load_skillsrc(destination) if original is not None else None
    except (SkillsrcError, OSError) as error:
        return _receipt("error", root, False, modules, [], [getattr(error, "code", "read_error")], fingerprint)
    try:
        discovery_ids = {question["id"] for question in discovery.get("questions", [])}
        reconciled = reconcile_skillsrc(existing, proposed, {key: value for key, value in answers.items() if key not in discovery_ids})
    except InitError as error:
        return _receipt("error", root, False, modules, [], [error.code], fingerprint)
    if reconciled["status"] == "conflict": return _receipt("conflict", root, False, modules, reconciled["questions"], [], fingerprint)
    if reconciled["status"] == "unchanged": return _receipt("unchanged", root, False, modules, [], [], fingerprint)
    if not write: return _receipt("preview", root, False, modules, [], [], fingerprint)
    try:
        atomic_write_skillsrc(root, destination, reconciled["document"], fingerprint, original)
    except Exception as error:
        return _receipt("error", root, False, modules, [], [getattr(error, "code", "write_error")], fingerprint)
    return _receipt(reconciled["status"], root, True, modules, [], [], fingerprint)


def _confined_output(root: Path, value: str) -> Path:
    candidate = Path(value)
    raw = str(candidate)
    if raw.startswith("\\\\") or raw.startswith("\\\\?\\") or any(":" in part for part in candidate.parts[1:]):
        raise ValueError("--output must be below exact docs/to_do")
    resolved = candidate.resolve()
    required = root.resolve() / "docs" / "to_do"
    try:
        resolved.relative_to(required)
    except ValueError as error:
        raise ValueError("--output must be below exact docs/to_do") from error
    return resolved


def _atomic_json(root: Path, path: Path, value: Mapping[str, Any]) -> None:
    path = _confined_output(root, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _confined_output(root, str(path))
    handle, temporary_name = tempfile.mkstemp(prefix=".skillsrc-init.", suffix=".tmp", dir=path.parent); temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project", required=True); parser.add_argument("--write", action="store_true"); parser.add_argument("--answers"); parser.add_argument("--output")
    args = parser.parse_args(); root = Path(args.project)
    try:
        answers = {} if not args.answers else json.loads(Path(args.answers).read_text(encoding="utf-8"))
        if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()): raise ValueError("answers must be a string mapping")
        if not root.is_dir(): raise ValueError("--project does not exist")
        output = _confined_output(root, args.output) if args.output else None
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "errors": [str(error)]}, ensure_ascii=False)); return 2
    report = ensure_skillsrc(root, answers, args.write)
    if output:
        try:
            _atomic_json(root, output, report)
        except OSError as error:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"receipt_error": _sanitize(str(error))}, ensure_ascii=False), file=sys.stderr)
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"preview", "created", "updated", "unchanged"} else 3 if report["status"] in {"needs_input", "conflict"} else 1


if __name__ == "__main__": raise SystemExit(main())
