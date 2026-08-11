#!/usr/bin/env python3
"""Safely compile discovery evidence into a portable .skillsrc manifest."""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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


class OutputConfinementError(InitError):
    def __init__(self, message: str):
        super().__init__("output_confined", message)


@dataclass(frozen=True)
class VerifiedOutputTarget:
    project_root: Path
    relative_parent: tuple[str, ...]
    parent: Path
    parent_identity: tuple[int, int, int | None]
    name: str
    guards: tuple[int, ...]

    @property
    def destination(self) -> Path:
        return self.parent / self.name

    @property
    def parent_guard(self) -> int:
        return self.guards[-1]

    def close(self) -> None:
        for guard in reversed(self.guards):
            _close_directory_guard(guard)


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
            return {"status": "conflict", "document": existing, "questions": [_public_question(q) for q in questions]}
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


def _receipt(status: str, root: Path, written: bool, modules: list[dict[str, Any]], questions: list[dict[str, Any]], errors: list[str], fingerprint: str, exit_code: int | None = None) -> dict[str, Any]:
    operation = {"created": "create", "updated": "update"}.get(status)
    receipt = {
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


def ensure_skillsrc(project_dir: Path, answers: Mapping[str, str], write: bool) -> dict[str, Any]:
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
    try:
        proposed = compile_skillsrc(discovery, answers)
    except NeedsInput as error:
        return _receipt("needs_input", root, False, modules, error.questions, [], fingerprint)
    except InitError as error:
        return _receipt("error", root, False, modules, [], [error.code], fingerprint, _input_exit_code(error.code))
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
    except (SkillsrcError, OSError, TypeError, ValueError) as error:
        code = getattr(error, "code", "read_error")
        return _receipt("error", root, False, modules, [], [code], fingerprint, _input_exit_code(code))
    try:
        discovery_ids = {question["id"] for question in discovery.get("questions", [])}
        reconciled = reconcile_skillsrc(existing, proposed, {key: value for key, value in answers.items() if key not in discovery_ids})
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
        atomic_write_skillsrc(root, destination, reconciled["document"], fingerprint, original)
    except Exception as error:
        return _receipt("error", root, False, _modules_on_disk(destination), [], [getattr(error, "code", "write_error")], fingerprint, 1)
    return _receipt(reconciled["status"], root, True, actual_modules, [], [], fingerprint)


def _output_relative(root: Path, value: str | Path) -> tuple[Path, tuple[str, ...]]:
    raw = str(value)
    if raw.startswith(("\\\\", "//", "\\\\?\\")):
        raise OutputConfinementError("--output must be below exact docs/to_do")
    project_root = root.resolve(strict=True)
    candidate = Path(raw)
    try:
        relative = candidate.relative_to(project_root) if candidate.is_absolute() else candidate
    except ValueError as error:
        raise OutputConfinementError("--output must be below exact docs/to_do") from error
    parts = relative.parts
    if len(parts) < 3 or parts[:2] != ("docs", "to_do") or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise OutputConfinementError("--output must be below exact docs/to_do")
    return project_root, tuple(parts)


def _is_link_or_reparse(path: Path) -> bool:
    details = os.stat(path, follow_symlinks=False)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & 0x400)


def _parent_identity(path: Path) -> tuple[int, int, int | None]:
    details = os.stat(path, follow_symlinks=False)
    return details.st_dev, details.st_ino, getattr(details, "st_file_attributes", None)


def _open_directory_guard(path: Path) -> int:
    before = _parent_identity(path)
    if _is_link_or_reparse(path):
        raise OutputConfinementError("--output parent is a symlink or reparse point")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(path), 0x100081, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError()
        guard = int(handle)
    else:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        guard = os.open(path, flags)
    try:
        if _parent_identity(path) != before or _is_link_or_reparse(path):
            raise OutputConfinementError("--output parent changed during validation")
        if os.name != "nt":
            details = os.fstat(guard)
            if (details.st_dev, details.st_ino) != before[:2]:
                raise OutputConfinementError("--output parent changed during validation")
    except Exception:
        _close_directory_guard(guard)
        raise
    return guard


def _close_directory_guard(guard: int) -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.CloseHandle(guard)
    else:
        os.close(guard)


def _open_posix_child_guard(parent_guard: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    guard = os.open(name, flags, dir_fd=parent_guard)
    try:
        if not stat.S_ISDIR(os.fstat(guard).st_mode):
            raise OutputConfinementError("--output parent component is not a directory")
    except Exception:
        os.close(guard)
        raise
    return guard


def _open_windows_replacement_handle(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x00010000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    return int(handle)


def _replace_output(record: VerifiedOutputTarget, temporary: Path, replacement_handle: int | None) -> None:
    if os.name != "nt":
        os.replace(temporary.name, record.name, src_dir_fd=record.parent_guard, dst_dir_fd=record.parent_guard)
        return

    import ctypes
    from ctypes import wintypes

    if replacement_handle is None:
        raise OSError("missing replacement handle")

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    encoded_name = record.name.encode("utf-16-le")
    size = FileRenameInfo.FileName.offset + len(encoded_name)
    buffer = ctypes.create_string_buffer(size)
    info = FileRenameInfo.from_buffer(buffer)
    info.ReplaceIfExists = 1
    info.RootDirectory = record.parent_guard
    info.FileNameLength = len(encoded_name)
    ctypes.memmove(ctypes.addressof(buffer) + FileRenameInfo.FileName.offset, encoded_name, len(encoded_name))
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    status_block = IoStatusBlock()
    set_information = ctypes.windll.ntdll.NtSetInformationFile
    set_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(IoStatusBlock), wintypes.LPVOID, wintypes.ULONG, ctypes.c_int]
    set_information.restype = wintypes.LONG
    status = set_information(replacement_handle, ctypes.byref(status_block), buffer, size, 10)
    if status != 0:
        to_dos_error = ctypes.windll.ntdll.RtlNtStatusToDosError
        to_dos_error.argtypes = [wintypes.LONG]
        to_dos_error.restype = wintypes.ULONG
        raise ctypes.WinError(to_dos_error(status))


def _remove_owned_temporary(project_root: Path, temporary: Path, identity: tuple[int, int, int | None]) -> None:
    candidates = [temporary]
    if not temporary.exists():
        try:
            candidates.extend(project_root.rglob(temporary.name))
        except OSError:
            return
    for candidate in candidates:
        try:
            if candidate.is_file() and _parent_identity(candidate) == identity:
                candidate.unlink()
                return
        except OSError:
            continue


def _verified_output_target(root: Path, value: str | Path) -> VerifiedOutputTarget:
    project_root, parts = _output_relative(root, value)
    parent_parts = parts[:-1]
    current = project_root
    guards: list[int] = []
    try:
        guards.append(_open_directory_guard(current))
        for part in parent_parts:
            current /= part
            if os.name == "nt":
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                guards.append(_open_directory_guard(current))
            else:
                try:
                    os.mkdir(part, dir_fd=guards[-1])
                except FileExistsError:
                    pass
                guards.append(_open_posix_child_guard(guards[-1], part))
        parent = current.resolve(strict=True)
        if os.name != "nt":
            guarded = os.fstat(guards[-1])
            if (guarded.st_dev, guarded.st_ino) != _parent_identity(parent)[:2]:
                raise OutputConfinementError("--output parent changed during validation")
        required = (project_root / "docs" / "to_do").resolve(strict=True)
        parent.relative_to(required)
    except (OSError, ValueError) as error:
        for guard in reversed(guards):
            _close_directory_guard(guard)
        if isinstance(error, OutputConfinementError):
            raise
        raise OutputConfinementError("--output must be below exact docs/to_do") from error
    return VerifiedOutputTarget(project_root, parent_parts, parent, _parent_identity(parent), parts[-1], tuple(guards))


def _verify_output_parent(record: VerifiedOutputTarget, temporary: Path) -> None:
    requested_parent = record.project_root.joinpath(*record.relative_parent)
    current = record.project_root
    try:
        for part in record.relative_parent:
            current /= part
            if _is_link_or_reparse(current):
                raise OutputConfinementError("--output parent is a symlink or reparse point")
        resolved = requested_parent.resolve(strict=True)
        if not os.path.samefile(resolved, record.parent) or _parent_identity(resolved) != record.parent_identity:
            raise OutputConfinementError("--output parent changed during receipt write")
        if temporary.parent.resolve(strict=True) != record.parent or _parent_identity(temporary.parent) != record.parent_identity:
            raise OutputConfinementError("receipt temporary parent changed during write")
    except (OSError, ValueError) as error:
        if isinstance(error, OutputConfinementError):
            raise
        raise OutputConfinementError("--output parent changed during receipt write") from error


def _confined_output(root: Path, value: str) -> Path:
    project_root, parts = _output_relative(root, value)
    return project_root.joinpath(*parts)


def _atomic_json_target(record: VerifiedOutputTarget, value: Mapping[str, Any]) -> None:
    handle, temporary_name = tempfile.mkstemp(prefix=".skillsrc-init.", suffix=".tmp", dir=record.parent)
    temporary = Path(temporary_name)
    temporary_identity = _parent_identity(temporary)
    replacement_handle: int | None = None
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        if os.name == "nt":
            replacement_handle = _open_windows_replacement_handle(temporary)
        _verify_output_parent(record, temporary)
        _replace_output(record, temporary, replacement_handle)
        if os.name != "nt":
            try:
                _verify_output_parent(record, temporary)
            except OutputConfinementError:
                try:
                    written = os.stat(record.name, dir_fd=record.parent_guard, follow_symlinks=False)
                    if (written.st_dev, written.st_ino) == temporary_identity[:2] and stat.S_ISREG(written.st_mode):
                        os.unlink(record.name, dir_fd=record.parent_guard)
                except FileNotFoundError:
                    pass
                raise
    finally:
        if replacement_handle is not None:
            _close_directory_guard(replacement_handle)
        if os.name == "nt":
            _remove_owned_temporary(record.project_root, temporary, temporary_identity)
        else:
            try:
                os.unlink(temporary.name, dir_fd=record.parent_guard)
            except FileNotFoundError:
                pass


def _atomic_json(root: Path, path: Path, value: Mapping[str, Any]) -> None:
    record = _verified_output_target(root, path)
    try:
        _atomic_json_target(record, value)
    finally:
        record.close()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project", required=True); parser.add_argument("--write", action="store_true"); parser.add_argument("--answers"); parser.add_argument("--output")
    args = parser.parse_args(); root = Path(args.project)
    output_target: VerifiedOutputTarget | None = None
    try:
        answers = {} if not args.answers else json.loads(Path(args.answers).read_text(encoding="utf-8"))
        if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()): raise ValueError("answers must be a string mapping")
        if not root.is_dir(): raise ValueError("--project does not exist")
        output = _confined_output(root, args.output) if args.output else None
        if output is not None:
            output_target = _verified_output_target(root, output)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "errors": [_sanitize(str(error))]}, ensure_ascii=False)); return 2
    try:
        report = ensure_skillsrc(root, answers, args.write)
    except (OSError, TypeError, ValueError, KeyError):
        report = _receipt("error", root.resolve(), False, [], [], ["read_error"], hashlib.sha256(b"").hexdigest(), 2)
    public_report = _public_report(report)
    if output_target is not None:
        try:
            _atomic_json(root, output_target.destination, public_report)
        except (OSError, ValueError) as error:
            print(json.dumps(public_report, ensure_ascii=False, indent=2))
            print(json.dumps({"receipt_error": _sanitize(str(error))}, ensure_ascii=False), file=sys.stderr)
            output_target.close()
            return 1
        output_target.close()
    print(json.dumps(public_report, ensure_ascii=False, indent=2))
    return 0 if public_report["status"] in {"preview", "created", "updated", "unchanged"} else 3 if public_report["status"] in {"needs_input", "conflict"} else int(report.get("_exit_code", 1))


if __name__ == "__main__": raise SystemExit(main())
