#!/usr/bin/env python3
"""Safely compile discovery evidence into a portable .skillsrc manifest."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence

import yaml

if __package__:
    from .discover_project import discover_project, project_fingerprint
    from .skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc
else:
    from discover_project import discover_project, project_fingerprint
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
    known = set()
    selected: list[tuple[str, object]] = []
    for question in questions:
        if not isinstance(question, Mapping) or not isinstance(question.get("id"), str):
            raise InitError("discovery_invalid", "discovery question is invalid")
        question_id = question["id"]; known.add(question_id)
        options = {item.get("id"): item for item in question.get("options", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
        answer = answers.get(question_id)
        if answer is None:
            unresolved.append(dict(question)); continue
        if answer not in options:
            raise InitError("answer_unknown", f"unknown option for {question_id}")
        selected.append((str(question.get("field")), options[answer].get("value")))
    if set(answers) - known:
        raise InitError("answer_unknown", "answer does not match a current question")
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


def _question(question_id: str, field: str, existing: object, detected: object) -> dict[str, Any]:
    return {"id": question_id, "field": field, "impact": "Changing an existing manifest value requires confirmation", "options": [{"id": "keep-existing", "value": existing, "evidence": []}, {"id": "use-detected", "value": detected, "evidence": []}]}


def _merge_additive(existing: Any, proposed: Any, field: str, questions: list[dict[str, Any]]) -> Any:
    if isinstance(existing, dict) and isinstance(proposed, dict):
        result = copy.deepcopy(existing)
        for key, value in proposed.items():
            result[key] = _merge_additive(result[key], value, f"{field}.{key}", questions) if key in result else copy.deepcopy(value)
        return result
    if existing == proposed or field.endswith(".detected_from"):
        return copy.deepcopy(existing if existing != proposed else proposed)
    questions.append(_question(f"replace:{field}", field, existing, proposed))
    return copy.deepcopy(existing)


def reconcile_skillsrc(existing: dict[str, Any] | None, proposed: dict[str, Any], answers: Mapping[str, str]) -> dict[str, Any]:
    if existing is None:
        return {"status": "created", "document": proposed, "questions": []}
    normalized = normalize_skillsrc(existing)
    existing_modules = normalized["modules"]
    proposed_modules = proposed["modules"]
    if existing.get("version") != "3.0":
        if len(proposed_modules) == 1 and _v2_matches_detected(existing_modules[0], proposed_modules[0]):
            return {"status": "unchanged", "document": existing, "questions": []}
        migration = {"id": "migrate-v2-to-v3", "field": "version", "impact": "Multiple or changed modules require v3", "options": [{"id": "keep-existing", "value": "2", "evidence": []}, {"id": "use-detected", "value": "3", "evidence": []}]}
        if answers.get(migration["id"]) != "use-detected":
            return {"status": "conflict", "document": existing, "questions": [migration]}
        if set(answers) != {migration["id"]}:
            raise InitError("answer_unknown", "answer does not match a current question")
        return {"status": "updated", "document": proposed, "questions": []}
    known = {module["id"]: module for module in existing_modules}
    merged = copy.deepcopy(existing)
    questions: list[dict[str, Any]] = []
    output = []
    for module in proposed_modules:
        old = known.pop(module["id"], None)
        output.append(copy.deepcopy(module) if old is None else _merge_additive(old, module, f"modules.{module['id']}", questions))
    if known:
        for module_id, module in known.items():
            questions.append(_question(f"remove:modules.{module_id}", f"modules.{module_id}", module, None))
            output.append(copy.deepcopy(module))
    if questions:
        unanswered = [q for q in questions if q["id"] not in answers]
        if set(answers) - {q["id"] for q in questions}:
            raise InitError("answer_unknown", "answer does not match a current question")
        if unanswered:
            return {"status": "conflict", "document": existing, "questions": questions}
        for question in questions:
            if answers[question["id"]] not in {"keep-existing", "use-detected"}:
                raise InitError("answer_unknown", f"unknown option for {question['id']}")
        if any(answers[q["id"]] == "keep-existing" for q in questions):
            return {"status": "unchanged", "document": existing, "questions": []}
    merged["modules"] = output
    return {"status": "unchanged" if merged == existing else "updated", "document": merged, "questions": []}


def _module_equivalent(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    old = copy.deepcopy(dict(old)); new = copy.deepcopy(dict(new))
    old.pop("detected_from", None); new.pop("detected_from", None)
    return old == new


def _v2_matches_detected(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """V2 has no module envelope; retain it when its core stack agrees."""
    if old.get("id") != "root" or new.get("id") != "root" or old.get("root") != new.get("root"):
        return False
    old_stack, new_stack = old.get("stack", {}), new.get("stack", {})
    return all(old_stack.get(key) == new_stack.get(key) for key in ("language", "build_tool"))


def _payload(document: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False, default_flow_style=False).replace("\r\n", "\n").encode("utf-8")


def atomic_write_skillsrc(project_dir: Path, destination: Path, document: Mapping[str, Any], evidence_paths: Sequence[str], expected_fingerprint: str, expected_destination: bytes | None) -> None:
    if project_fingerprint(project_dir, evidence_paths) != expected_fingerprint:
        raise InitError("project_changed", "project manifests changed during initialization")
    handle, temporary_name = tempfile.mkstemp(prefix=".skillsrc.", suffix=".tmp", dir=project_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_payload(document)); stream.flush(); os.fsync(stream.fileno())
        load_skillsrc(temporary)
        current = destination.read_bytes() if destination.exists() else None
        if current != expected_destination:
            raise InitError("destination_changed", ".skillsrc changed during initialization")
        if project_fingerprint(project_dir, evidence_paths) != expected_fingerprint:
            raise InitError("project_changed", "project manifests changed during initialization")
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()


def _receipt(status: str, root: Path, written: bool, modules: list[dict[str, Any]], questions: list[dict[str, Any]], errors: list[str], fingerprint: str) -> dict[str, Any]:
    return {"status": status, "skillsrc_path": ".skillsrc", "written": written, "module_ids": [module["id"] for module in modules], "questions": questions, "changes": ([] if status in {"unchanged", "preview", "needs_input", "conflict", "error"} else [{"operation": status, "path": ".skillsrc"}]), "warnings": [], "errors": errors, "discovery_fingerprint": fingerprint}


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
        reconciled = reconcile_skillsrc(existing, proposed, answers)
    except InitError as error:
        return _receipt("error", root, False, modules, [], [error.code], fingerprint)
    if reconciled["status"] == "conflict": return _receipt("conflict", root, False, modules, reconciled["questions"], [], fingerprint)
    if reconciled["status"] == "unchanged": return _receipt("unchanged", root, False, modules, [], [], fingerprint)
    if not write: return _receipt("preview", root, False, modules, [], [], fingerprint)
    evidence = [path for module in modules for path in module.get("detected_from", [])]
    try:
        atomic_write_skillsrc(root, destination, reconciled["document"], evidence, fingerprint, original)
    except Exception as error:
        return _receipt("error", root, False, modules, [], [getattr(error, "code", "write_error")], fingerprint)
    return _receipt(reconciled["status"], root, True, modules, [], [], fingerprint)


def _confined_output(root: Path, value: str) -> Path:
    candidate = Path(value)
    if str(candidate).startswith("\\\\"):
        raise ValueError("--output must be below exact docs/to_do")
    resolved = candidate.resolve()
    required = root.resolve() / "docs" / "to_do"
    try: resolved.relative_to(required)
    except ValueError as error: raise ValueError("--output must be below exact docs/to_do") from error
    return resolved


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    if output: _atomic_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"preview", "created", "updated", "unchanged"} else 3 if report["status"] in {"needs_input", "conflict"} else 1


if __name__ == "__main__": raise SystemExit(main())
