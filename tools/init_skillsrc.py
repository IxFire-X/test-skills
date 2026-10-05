#!/usr/bin/env python3
"""Safely compile discovery evidence into a portable .skillsrc manifest."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

import yaml

if __package__:
    from .confined_output import ConfinedOutputTarget, OutputConfinementError, acquire_confined_output, create_confined_bytes_exclusive, read_confined_bytes
    from .discover_project import SOURCE_SUFFIXES, _ok, _source_dir_has_code, discover_project
    from .schema_validation import StrictJsonError, load_json_strict
    from .stack_catalog import is_ignored_dir_name
    from .skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc
else:
    from confined_output import ConfinedOutputTarget, OutputConfinementError, acquire_confined_output, create_confined_bytes_exclusive, read_confined_bytes
    from discover_project import SOURCE_SUFFIXES, _ok, _source_dir_has_code, discover_project
    from schema_validation import StrictJsonError, load_json_strict
    from stack_catalog import is_ignored_dir_name
    from skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc


__all__ = ["OutputConfinementError"]


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


def compile_skillsrc(discovery: Mapping[str, Any], answers: Mapping[str, str], project_dir: Path) -> dict[str, Any]:
    questions = discovery.get("questions", [])
    if not isinstance(questions, list):
        raise InitError("discovery_invalid", "discovery questions are invalid")
    unresolved: list[dict[str, Any]] = []
    known: set[str] = set()
    selected: list[tuple[str, object]] = []
    for question in questions:
        if not isinstance(question, Mapping) or not isinstance(question.get("id"), str) or not isinstance(question.get("field"), str):
            raise InitError("discovery_invalid", "discovery question is invalid")
        question_id = question["id"]
        known.add(question_id)
        raw_options = question.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            raise InitError("discovery_invalid", "discovery question options are invalid")
        options = {item.get("id"): item for item in raw_options if isinstance(item, Mapping) and isinstance(item.get("id"), str) and "value" in item}
        if len(options) != len(raw_options):
            raise InitError("discovery_invalid", "discovery question option is invalid")
        answer = answers.get(question_id)
        if answer is None:
            unresolved.append(dict(question)); continue
        if answer not in options:
            raise InitError("answer_unknown", f"unknown option for {question_id}")
        selected.append((question["field"], options[answer]["value"]))
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
    if any(not isinstance(module, dict) or not isinstance(module.get("id"), str) for module in modules):
        raise InitError("discovery_invalid", "discovery module is invalid")
    by_id = {module["id"]: module for module in modules}
    if len(by_id) != len(modules):
        raise InitError("discovery_invalid", "discovery module ids are invalid")
    for field, value in selected:
        parts = field.split(".")
        if len(parts) < 4 or parts[1] not in by_id:
            raise InitError("discovery_invalid", "question does not refer to a discovered module")
        _set_field(by_id[parts[1]], field, value)
    for module in modules:
        if isinstance(module, dict):
            module.pop("readiness", None)
            _close_discovered_test(module, project_dir)
    return {"schema_version": "5.0.0", "version": "3.0", "project": {"name": discovery.get("project_name", "project")}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": modules}


def _close_discovered_test(module: dict[str, Any], project_dir: Path) -> None:
    """Emit only the frozen closed execution tuple, or omit execution config."""
    test = module.get("test")
    stack = module.get("stack")
    if not isinstance(test, Mapping) or not isinstance(stack, Mapping):
        module.pop("test", None)
        return
    framework = test.get("framework")
    language = stack.get("language")
    build_tool = stack.get("build_tool")
    if language == "python" and framework == "pytest":
        module["test"] = {
            "framework": "pytest",
            "adapter_id": "pytest:selected-symbols-v1",
            "interpreter": ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python",
            "build_profile": "default",
            "adapter_parameters": {},
        }
        return
    wrapper_names = {"maven": ("mvnw", "mvnw.cmd"), "gradle": ("gradlew", "gradlew.bat")}
    names = wrapper_names.get(build_tool)
    wrapper = names[os.name == "nt"] if names else None
    root = project_dir.resolve()
    target = root / str(module.get("root", ".")) / wrapper if wrapper else None
    adapter_ids = {
        "maven": "maven-wrapper:selected-symbols-v1",
        "gradle": "gradle-wrapper:selected-symbols-v1",
    }
    if language == "java" and framework == "junit5" and build_tool in adapter_ids and target is not None and target.is_file() and _ok(root, target) and not _is_reparse(target) and (os.name == "nt" or os.access(target, os.X_OK)):
        module["test"] = {
            "framework": "junit5",
            "adapter_id": adapter_ids[build_tool],
            "wrapper": wrapper,
            "build_profile": "default",
            "adapter_parameters": {},
        }
        return
    if language == "java" and framework == "junit5" and build_tool == "maven":
        from tools.project_inventory import InventoryError, system_maven_path

        try:
            executable = system_maven_path("mvn")
        except (InventoryError, OSError):
            pass
        else:
            module["test"] = {
                "framework": "junit5", "adapter_id": "maven:selected-symbols-v1",
                "executable": str(executable), "build_profile": "default", "adapter_parameters": {},
            }
            return
    module.pop("test", None)


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
    question["_existing"] = copy.deepcopy(existing)
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
        unsupported = _v2_unsupported_fields(existing)
        if unsupported:
            question = _question("migrate-v2-preservation", "version", "replace")
            question["impact"] = "This v2 manifest contains fields without a lossless v3 representation"
            question["options"] = [{"id": "keep-existing", "value": "keep-existing", "evidence": []}]
            if set(answers) - {question["id"]}:
                raise InitError("answer_unknown", "answer does not match a current reconciliation question")
            if question["id"] in answers:
                if answers[question["id"]] != "keep-existing":
                    raise InitError("answer_unknown", "unknown preservation option")
                return {"status": "unchanged", "document": existing, "questions": []}
            return {"status": "conflict", "document": existing, "questions": [question]}
        if len(proposed_modules) == 1 and _v2_matches_detected(existing_modules[0], proposed_modules[0]):
            if answers:
                raise InitError("answer_unknown", "answer does not match a current reconciliation question")
            return {"status": "unchanged", "document": existing, "questions": []}
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
        for question in questions:
            if question["id"] in answers and answers[question["id"]] not in {"keep-existing", "use-detected"}:
                raise InitError("answer_unknown", f"unknown option for {question['id']}")
        unanswered = [q for q in questions if q["id"] not in answers]
        if unanswered:
            return {"status": "conflict", "document": existing, "questions": [_explained_question(unanswered[0])]}
        for question in questions:
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


def _safe_summary(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def _explained_question(question: Mapping[str, Any]) -> dict[str, Any]:
    public = _public_question(question)
    existing = question.get("_existing")
    detected = question.get("_detected")
    if detected is not None:
        public["options"] = [
            {"id": "keep-existing", "value": "keep-existing", "evidence": [f"keep existing {question.get('field')}: {_safe_summary(existing)}"]},
            {"id": "use-detected", "value": "use-detected", "evidence": [f"use detected {question.get('field')}: {_safe_summary(detected)}"]},
        ]
    elif question.get("operation") == "remove":
        public["options"] = [
            {"id": "keep-existing", "value": "keep-existing", "evidence": [f"keep module {question.get('_module_id')}"]},
            {"id": "use-detected", "value": "use-detected", "evidence": [f"remove module {question.get('_module_id')}"]},
        ]
    return public


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


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _structural_diff(existing: Any, proposed: Any, path: str = "") -> list[dict[str, str]]:
    """Return only paths and operations; never serialise configuration values."""
    if isinstance(existing, Mapping) and isinstance(proposed, Mapping):
        changes: list[dict[str, str]] = []
        for key in sorted(set(existing) | set(proposed), key=str):
            child = f"{path}/{key}".replace("//", "/")
            if key not in existing:
                changes.append({"path": child, "operation": "add"})
            elif key not in proposed:
                changes.append({"path": child, "operation": "remove"})
            else:
                changes.extend(_structural_diff(existing[key], proposed[key], child))
        return changes
    if isinstance(existing, list) and isinstance(proposed, list):
        changes = []
        for index in range(max(len(existing), len(proposed))):
            child = f"{path}/{index}"
            if index >= len(existing):
                changes.append({"path": child, "operation": "add"})
            elif index >= len(proposed):
                changes.append({"path": child, "operation": "remove"})
            else:
                changes.extend(_structural_diff(existing[index], proposed[index], child))
        return changes
    return [] if existing == proposed else [{"path": path or "/", "operation": "replace"}]


def _proposal(existing: Mapping[str, Any], original: bytes, proposed: Mapping[str, Any]) -> dict[str, Any]:
    proposed_bytes = _payload(proposed)
    return {
        "original_digest": _digest(original),
        "proposed_digest": _digest(proposed_bytes),
        "structural_diff": _structural_diff(existing, proposed),
    }


def _replacement_is_bound(approval: Mapping[str, Any] | None, proposal: Mapping[str, Any]) -> bool:
    if not isinstance(approval, Mapping) or set(approval) != {"approval_id", "original_digest", "proposed_digest"}:
        return False
    approval_id = approval.get("approval_id")
    return (
        isinstance(approval_id, str)
        and bool(re.fullmatch(r"[A-Za-z0-9._:-]+", approval_id))
        and approval.get("original_digest") == proposal["original_digest"]
        and approval.get("proposed_digest") == proposal["proposed_digest"]
    )


def _preserve_immutable_evidence(project_root: Path, directory: Path | None, original: bytes) -> None:
    if directory is None:
        raise InitError("replacement_unbound", "immutable evidence directory is required")
    project = Path(project_root).resolve(strict=True)
    candidate = Path(directory)
    directory = candidate if candidate.is_absolute() else project / candidate
    try:
        relative = directory.absolute().relative_to(project)
    except ValueError as error:
        raise InitError("evidence_write_error", "immutable evidence directory escapes project") from error
    probe = project
    for part in relative.parts:
        probe /= part
        if not probe.exists() or _is_reparse(probe):
            raise InitError("evidence_write_error", "immutable evidence directory is unsafe")
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise InitError("evidence_write_error", "immutable evidence directory is unsafe")
    destination = directory / (hashlib.sha256(original).hexdigest() + ".skillsrc")
    try:
        create_confined_bytes_exclusive(project, directory, destination, original)
        read_back = read_confined_bytes(project, directory, destination)
    except (OSError, OutputConfinementError, ValueError) as error:
        raise InitError("evidence_write_error", "immutable evidence write failed") from error
    if read_back != original:
        raise InitError("evidence_write_error", "immutable evidence read-back failed")


def atomic_write_skillsrc(project_dir: Path, destination: Path, document: Mapping[str, Any], expected_fingerprint: str, expected_destination: bytes | None, *, before_replace: Any = None) -> bytes:
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
        if before_replace is not None:
            before_replace()
        os.replace(temporary, destination)
        read_back = destination.read_bytes()
        load_skillsrc(destination)
        return read_back
    finally:
        if temporary.exists(): temporary.unlink()


def _receipt(status: str, root: Path, written: bool, modules: list[dict[str, Any]], questions: list[dict[str, Any]], errors: list[str], fingerprint: str, exit_code: int | None = None, *, proposal: Mapping[str, Any] | None = None, read_back: bytes | None = None) -> dict[str, Any]:
    operation = {"created": "create", "updated": "update"}.get(status)
    receipt = {
        "schema_version": "5.0.0",
        "status": status,
        "skillsrc_path": ".skillsrc",
        "written": written,
        "module_ids": [module["id"] for module in modules if isinstance(module, Mapping) and isinstance(module.get("id"), str)],
        "questions": questions,
        "changes": [] if operation is None else [{"operation": operation, "path": ".skillsrc"}],
        "warnings": [],
        "errors": errors,
        "discovery_fingerprint": fingerprint,
    }
    receipt = _sanitize(receipt)
    if proposal is not None:
        receipt["proposal"] = copy.deepcopy(dict(proposal))
    if read_back is not None:
        receipt["read_back_digest"] = _digest(read_back)
    if exit_code is not None:
        receipt["_exit_code"] = exit_code
    return receipt


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@", r"\1[redacted]@", value)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    return value


def _public_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if not key.startswith("_")}


def _input_exit_code(code: str) -> int:
    return 2 if code in {"answer_unknown", "discovery_invalid", "invalid_encoding", "invalid_yaml", "invalid_shape", "schema_invalid", "read_error"} else 1


def _safe_fingerprint(value: Any) -> str:
    candidate = str(value)
    return candidate if re.fullmatch(r"[0-9a-f]{64}", candidate) else hashlib.sha256(b"").hexdigest()


def _modules_on_disk(destination: Path) -> list[dict[str, Any]]:
    if not destination.exists():
        return []
    try:
        return normalize_skillsrc(load_skillsrc(destination))["modules"]
    except (OSError, SkillsrcError, ValueError):
        return []


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)


def _resolve_explicit_target(root: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    portable = value.replace("\\", "/")
    raw = Path(portable)
    windows = PureWindowsPath(value)
    if (
        not portable
        or raw.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} or ":" in part or is_ignored_dir_name(part) for part in portable.split("/"))
    ):
        return None
    candidate = root / raw
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    probe = candidate
    while True:
        if _is_reparse(probe):
            return None
        if probe == root:
            break
        probe = probe.parent
    return resolved


def _valid_explicit_target(root: Path, modules: list[dict[str, Any]], value: str | None) -> bool:
    resolved = _resolve_explicit_target(root, value)
    if resolved is None:
        return False
    for module in modules:
        module_root = root / str(module.get("root") or ".")
        paths = module.get("paths") if isinstance(module.get("paths"), Mapping) else {}
        feature_sources = module.get("feature_sources") if isinstance(module.get("feature_sources"), Mapping) else {}
        language = (module.get("stack") or {}).get("language") if isinstance(module.get("stack"), Mapping) else None
        suffixes = SOURCE_SUFFIXES.get(language, frozenset())
        sources = list(paths.get("source") or []) + list(feature_sources.get("source") or [])
        for source in sources:
            source_root = module_root / str(source)
            try:
                source_parts = source_root.relative_to(root).parts
            except ValueError:
                continue
            if any(is_ignored_dir_name(part) for part in source_parts) or _is_reparse(source_root):
                continue
            try:
                resolved.relative_to(source_root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                return resolved.suffix.lower() in suffixes
            return resolved.is_dir() and _source_dir_has_code(root, resolved, suffixes)
    return False


def _existing_discovery_answers(existing: Mapping[str, Any] | None, discovery: Mapping[str, Any], answers: Mapping[str, str]) -> dict[str, str]:
    effective = dict(answers)
    if existing is None:
        return effective
    try:
        modules = {module["id"]: module for module in normalize_skillsrc(existing)["modules"]}
    except (SkillsrcError, TypeError, ValueError, KeyError):
        return effective
    for question in discovery.get("questions", []):
        if not isinstance(question, Mapping) or question.get("id") in effective:
            continue
        parts = str(question.get("field", "")).split(".")
        if len(parts) < 4 or parts[0] != "modules" or parts[1] not in modules:
            continue
        current: Any = modules[parts[1]]
        for part in parts[2:]:
            if not isinstance(current, Mapping):
                break
            current = current.get(part)
        else:
            for option in question.get("options", []):
                if isinstance(option, Mapping) and current in {option.get("id"), option.get("value")}:
                    effective[str(question["id"])] = str(option["id"])
                    break
    return effective


def _authority_question() -> dict[str, Any]:
    return {
        "id": "skillsrc:replace",
        "field": ".skillsrc",
        "impact": "Execution-significant .skillsrc drift requires explicit confirmation",
        "operation": "replace",
        "options": [
            {"id": "keep-existing", "value": "keep-existing", "evidence": []},
            {"id": "replace-proposed", "value": "replace-proposed", "evidence": []},
        ],
    }


def ensure_skillsrc(project_dir: Path, answers: Mapping[str, str], write: bool, *, explicit_target: str | None = None, replacement_approval: Mapping[str, Any] | None = None, immutable_evidence_dir: Path | None = None) -> dict[str, Any]:
    root = project_dir.resolve(); destination = root / ".skillsrc"
    try:
        discovery = discover_project(root)
    except (OSError, TypeError, ValueError, KeyError):
        return _receipt("error", root, False, [], [], ["read_error"], hashlib.sha256(b"").hexdigest(), 2)
    if not isinstance(discovery, Mapping):
        return _receipt("error", root, False, [], [], ["discovery_invalid"], hashlib.sha256(b"").hexdigest(), 2)
    fingerprint = _safe_fingerprint(discovery.get("fingerprint"))
    raw_modules = discovery.get("modules", [])
    modules = list(raw_modules) if isinstance(raw_modules, list) else []
    if discovery.get("status") == "error":
        raw_errors = discovery.get("errors", [])
        errors = [str(error) for error in raw_errors] if isinstance(raw_errors, list) and raw_errors else ["discovery_invalid"]
        return _receipt("error", root, False, modules, [], errors, fingerprint, 2)
    if explicit_target is not None and _resolve_explicit_target(root, explicit_target) is None:
        return _receipt("error", root, False, modules, [], ["source_target_invalid"], fingerprint, 2)
    try:
        if destination.exists():
            if not destination.is_file():
                raise OSError(".skillsrc is not a regular file")
            original = destination.read_bytes()
        else:
            original = None
    except OSError:
        return _receipt("error", root, False, modules, [], ["read_error"], fingerprint, 2)
    try:
        existing = load_skillsrc(destination) if original is not None else None
        existing_modules = normalize_skillsrc(existing)["modules"] if existing is not None else []
    except (SkillsrcError, OSError, TypeError, ValueError) as error:
        code = getattr(error, "code", "read_error")
        return _receipt("error", root, False, modules, [], [code], fingerprint, _input_exit_code(code))
    working_discovery = copy.deepcopy(discovery)
    if explicit_target is not None and not _valid_explicit_target(root, existing_modules + modules, explicit_target):
        return _receipt("error", root, False, modules, [], ["source_target_invalid"], fingerprint, 2)
    declared_existing = {
        str(module.get("id"))
        for module in existing_modules
        if list((module.get("paths") or {}).get("source") or [])
        or list((module.get("feature_sources") or {}).get("source") or [])
    }
    working_discovery["questions"] = [
        question
        for question in working_discovery.get("questions", [])
        if not (
            isinstance(question, Mapping)
            and str(question.get("field", "")).endswith(".paths.source")
            and str(question.get("field", "")).split(".")[1] in declared_existing
        )
    ]
    source_questions = [question for question in working_discovery.get("questions", []) if isinstance(question, Mapping) and str(question.get("field", "")).endswith(".paths.source")]
    if source_questions:
        questions = [question for question in working_discovery.get("questions", []) if isinstance(question, Mapping)]
        if not questions:
            return _receipt("error", root, False, modules, [], ["discovery_invalid"], fingerprint, 2)
        known = {question["id"] for question in questions if isinstance(question.get("id"), str)}
        extra = [key for key in answers if key not in known and not str(key).startswith(("replace:", "remove:", "migrate-"))]
        if extra:
            return _receipt("error", root, False, modules, [], ["answer_unknown"], fingerprint, _input_exit_code("answer_unknown"))
        return _receipt("needs_input", root, False, modules, questions, [], fingerprint)
    effective_answers = _existing_discovery_answers(existing, working_discovery, answers)
    try:
        proposed = compile_skillsrc(working_discovery, effective_answers, root)
    except NeedsInput as error:
        return _receipt("needs_input", root, False, modules, error.questions, [], fingerprint)
    except InitError as error:
        return _receipt("error", root, False, modules, [], [error.code], fingerprint, _input_exit_code(error.code))
    if existing is not None:
        proposal = _proposal(existing, original, proposed)
        if not proposal["structural_diff"]:
            return _receipt("unchanged", root, False, normalize_skillsrc(existing)["modules"], [], [], fingerprint, proposal=proposal)
        if not _replacement_is_bound(replacement_approval, proposal):
            if replacement_approval is not None:
                return _receipt("error", root, False, normalize_skillsrc(existing)["modules"], [], ["replacement_unbound"], fingerprint, proposal=proposal, exit_code=2)
            return _receipt("needs_input", root, False, normalize_skillsrc(existing)["modules"], [_authority_question()], [], fingerprint, proposal=proposal)
        if not write:
            return _receipt("preview", root, False, normalize_skillsrc(existing)["modules"], [], [], fingerprint, proposal=proposal)
        try:
            read_back = atomic_write_skillsrc(
                root,
                destination,
                proposed,
                fingerprint,
                original,
                before_replace=lambda: _preserve_immutable_evidence(root, immutable_evidence_dir, original),
            )
        except Exception as error:
            return _receipt("error", root, False, normalize_skillsrc(existing)["modules"], [], [getattr(error, "code", "write_error")], fingerprint, 1, proposal=proposal)
        return _receipt("updated", root, True, normalize_skillsrc(proposed)["modules"], [], [], fingerprint, proposal=proposal, read_back=read_back)
    try:
        discovery_ids = {question["id"] for question in working_discovery.get("questions", [])}
        reconciled = reconcile_skillsrc(existing, proposed, {key: value for key, value in effective_answers.items() if key not in discovery_ids})
    except (InitError, SkillsrcError, TypeError, ValueError, KeyError) as error:
        if not isinstance(error, InitError):
            return _receipt("error", root, False, modules, [], [getattr(error, "code", "invalid_shape")], fingerprint, 2)
        return _receipt("error", root, False, modules, [], [error.code], fingerprint, _input_exit_code(error.code))
    try:
        actual_modules = normalize_skillsrc(reconciled["document"])["modules"]
    except (SkillsrcError, TypeError, ValueError, KeyError) as error:
        return _receipt("error", root, False, modules, [], [getattr(error, "code", "invalid_shape")], fingerprint, 2)
    if reconciled["status"] == "conflict": return _receipt("conflict", root, False, actual_modules, reconciled["questions"], [], fingerprint)
    if reconciled["status"] == "unchanged": return _receipt("unchanged", root, False, actual_modules, [], [], fingerprint)
    if not write: return _receipt("preview", root, False, actual_modules, [], [], fingerprint)
    try:
        read_back = atomic_write_skillsrc(root, destination, reconciled["document"], fingerprint, original)
    except Exception as error:
        return _receipt("error", root, False, _modules_on_disk(destination), [], [getattr(error, "code", "write_error")], fingerprint, 1)
    return _receipt(reconciled["status"], root, True, actual_modules, [], [], fingerprint, read_back=read_back)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(); parser.add_argument("--project", required=True); parser.add_argument("--write", action="store_true"); parser.add_argument("--answers"); parser.add_argument("--replacement-approval"); parser.add_argument("--immutable-evidence-dir"); parser.add_argument("--output")
    args = parser.parse_args(); root = Path(args.project)
    output_target: ConfinedOutputTarget | None = None
    try:
        answers = {} if not args.answers else load_json_strict(Path(args.answers))
        if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()): raise ValueError("answers must be a string mapping")
        approval = None if not args.replacement_approval else load_json_strict(Path(args.replacement_approval))
        if approval is not None and not isinstance(approval, dict): raise ValueError("replacement approval must be an object")
        if not root.is_dir(): raise ValueError("--project does not exist")
        if args.output:
            output_target = acquire_confined_output(root, args.output)
    except (OSError, StrictJsonError, ValueError) as error:
        print(json.dumps({"status": "error", "errors": [_sanitize(str(error))]}, ensure_ascii=False)); return 2
    try:
        report = ensure_skillsrc(root, answers, args.write, replacement_approval=approval, immutable_evidence_dir=Path(args.immutable_evidence_dir) if args.immutable_evidence_dir else None)
    except (OSError, TypeError, ValueError, KeyError):
        report = _receipt("error", root.resolve(), False, [], [], ["read_error"], hashlib.sha256(b"").hexdigest(), 2)
    public_report = _public_report(report)
    if output_target is not None:
        try:
            output_target.write_bytes(json.dumps(public_report, ensure_ascii=False, indent=2).encode("utf-8"))
        except (OSError, ValueError) as error:
            print(json.dumps(public_report, ensure_ascii=False, indent=2))
            print(json.dumps({"receipt_error": _sanitize(str(error))}, ensure_ascii=False), file=sys.stderr)
            return 1
        finally:
            output_target.close()
    print(json.dumps(public_report, ensure_ascii=False, indent=2))
    return 0 if public_report["status"] in {"preview", "created", "updated", "unchanged"} else 3 if public_report["status"] in {"needs_input", "conflict"} else int(report.get("_exit_code", 1))


if __name__ == "__main__": raise SystemExit(main())
