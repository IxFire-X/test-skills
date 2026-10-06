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
            "interpreter": "python",
            "build_profile": "default",
            "adapter_parameters": {},
        }
        return
    wrapper_names = {"maven": ("mvnw", "mvnw.cmd"), "gradle": ("gradlew", "gradlew.bat")}
    names = wrapper_names.get(build_tool)
    # The manifest keeps the logical wrapper name; the host launcher is chosen at run time.
    wrapper = names[0] if names else None
    root = project_dir.resolve()
    build_root = _detected_build_root(root, str(module.get("root", ".")), build_tool) if names else None
    target = root / build_root / names[os.name == "nt"] if names and build_root is not None else None
    multi_module = {} if build_root in {None, str(module.get("root", "."))} else {"build_root": build_root}
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
            **multi_module,
        }
        return
    if language == "java" and framework == "junit5" and build_tool == "maven":
        from tools.project_inventory import InventoryError, system_maven_path

        try:
            system_maven_path("mvn")
        except (InventoryError, OSError):
            pass
        else:
            module["test"] = {
                "framework": "junit5", "adapter_id": "maven:selected-symbols-v1",
                "executable": "mvn", "build_profile": "default", "adapter_parameters": {},
                **multi_module,
            }
            return
    module.pop("test", None)


def _maven_child_dirs(directory: Path) -> set[Path]:
    """Directories a reactor ``pom.xml`` aggregates, including profile-scoped modules."""
    import xml.etree.ElementTree as ET

    try:
        tree = ET.fromstring((directory / "pom.xml").read_bytes())
    except (OSError, ET.ParseError):
        return set()
    children: set[Path] = set()
    for element in tree.iter():
        if element.tag.rsplit("}", 1)[-1] != "module" or not (element.text or "").strip():
            continue
        child = directory / element.text.strip().replace("\\", "/")
        children.add((child.parent if child.name.endswith(".xml") else child).resolve())
    return children


def _detected_build_root(project_root: Path, module_root: str, build_tool: Any) -> str | None:
    """Project-relative directory the build must start from for this module.

    Gradle: the nearest directory, from the module upwards, holding ``settings.gradle(.kts)``.
    Maven: the outermost reactor whose ``<modules>`` chain reaches the module.
    Without such an ancestor the module is its own build root.
    """
    parts = [part for part in module_root.replace("\\", "/").split("/") if part not in {"", "."}]
    chain = [project_root.joinpath(*parts[:size]) for size in range(len(parts), -1, -1)]
    chosen = chain[0]
    if any(_is_reparse(directory) for directory in chain[:-1]):
        return None
    if build_tool == "gradle":
        chosen = next((directory for directory in chain if any((directory / name).is_file() for name in ("settings.gradle", "settings.gradle.kts"))), chain[0])
    elif build_tool == "maven":
        for directory in chain[1:]:
            if chosen.resolve() in _maven_child_dirs(directory):
                chosen = directory
    else:
        return None
    return chosen.relative_to(project_root).as_posix() or "."


_LEGACY_RUNTIME_NAMES = {
    "interpreter": ({".venv/bin/python", ".venv/Scripts/python.exe"}, "python"),
    "wrapper": ({"mvnw.cmd"}, "mvnw"),
}


def _runtime_names_equivalent(key: str, existing: Any, proposed: Any) -> bool:
    """An OS-specific name written by an older pack names the same runtime as the logical one."""
    if not isinstance(existing, str) or not isinstance(proposed, str) or existing == proposed:
        return False
    if key == "executable":
        name = PureWindowsPath(existing).name.casefold()
        return proposed == "mvn" and name in {"mvn", "mvn.cmd"} and (PureWindowsPath(existing).is_absolute() or existing.startswith("/"))
    if key == "wrapper" and (existing, proposed) == ("gradlew.bat", "gradlew"):
        return True
    legacy, logical = _LEGACY_RUNTIME_NAMES.get(key, (set(), None))
    return proposed == logical and existing in legacy


def _keep_equivalent_runtime_names(existing: Mapping[str, Any], proposed: dict[str, Any]) -> None:
    """Do not report a legacy OS-specific runtime path as configuration drift."""
    if existing.get("version") != "3.0" or not isinstance(existing.get("modules"), list):
        return
    known = {module.get("id"): module.get("test") for module in existing["modules"] if isinstance(module, Mapping)}
    for module in proposed.get("modules", []):
        old, new = known.get(module.get("id")), module.get("test")
        if not isinstance(old, Mapping) or not isinstance(new, dict) or old.get("adapter_id") != new.get("adapter_id"):
            continue
        for key in ("interpreter", "wrapper", "executable"):
            if _runtime_names_equivalent(key, old.get(key), new.get(key)):
                new[key] = old[key]


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


def _install_exclusive(temporary: Path, destination: Path) -> None:
    """Publish a complete new file without replacing an existing destination.

    A hard link is atomic and fails if the name exists. Where links are unsupported
    (some network or FAT volumes) an O_EXCL create keeps the exclusivity guarantee.
    """
    try:
        os.link(temporary, destination)
        return
    except FileExistsError as error:
        raise InitError("destination_changed", ".skillsrc changed during initialization") from error
    except (OSError, NotImplementedError, AttributeError):
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as error:
        raise InitError("destination_changed", ".skillsrc changed during initialization") from error
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(temporary.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Only this call created the file, so removing a partial write is safe.
        try:
            destination.unlink()
        except OSError:
            pass
        raise


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
        if expected_destination is None:
            # Creation must never overwrite a manifest that appeared after the check.
            _install_exclusive(temporary, destination)
        else:
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


def _apply_source_answer(root: Path, discovery: dict[str, Any], question: Mapping[str, Any], answer: str) -> bool:
    """Record an answered source root on its module; reject anything unsafe or empty."""
    parts = str(question.get("field", "")).split(".")
    module = next((item for item in discovery.get("modules", []) if isinstance(item, dict) and len(parts) == 4 and item.get("id") == parts[1]), None)
    portable = answer.replace("\\", "/").strip("/") if isinstance(answer, str) else ""
    if module is None or not portable:
        return False
    module_root = str(module.get("root") or ".")
    resolved = _resolve_explicit_target(root, portable if module_root == "." else f"{module_root}/{portable}")
    language = (module.get("stack") or {}).get("language") if isinstance(module.get("stack"), Mapping) else None
    suffixes = SOURCE_SUFFIXES.get(language, frozenset().union(*SOURCE_SUFFIXES.values()))
    if resolved is None or not resolved.is_dir() or not _source_dir_has_code(root, resolved, suffixes):
        return False
    module.setdefault("paths", {})["source"] = [portable]
    module["readiness"] = "source_ready"
    return True


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
    # A source root the manifest already declares answers the suppressed question, so a
    # manifest written from that answer is stable on the next run instead of "drifting".
    existing_sources = {str(module.get("id")): list((module.get("paths") or {}).get("source") or []) for module in existing_modules}
    for module in working_discovery.get("modules", []):
        if isinstance(module, dict) and module.get("readiness") != "source_ready" and existing_sources.get(str(module.get("id"))):
            module.setdefault("paths", {})["source"] = list(existing_sources[str(module.get("id"))])
    source_questions = [question for question in working_discovery.get("questions", []) if isinstance(question, Mapping) and str(question.get("field", "")).endswith(".paths.source")]
    answers = dict(answers)
    for question in list(source_questions):
        # The answer to a source question is the source directory itself, relative to the module root.
        answer = answers.get(str(question.get("id")))
        if answer is None or answer == "provide-source-root":
            continue
        if not _apply_source_answer(root, working_discovery, question, answer):
            return _receipt("error", root, False, modules, [], ["source_target_invalid"], fingerprint, 2)
        answers.pop(str(question.get("id")))
        source_questions.remove(question)
        working_discovery["questions"] = [item for item in working_discovery["questions"] if item is not question]
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
        _keep_equivalent_runtime_names(existing, proposed)
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
    # No manifest exists here: an existing one always takes the authority branch above,
    # so there is nothing to merge or migrate and every remaining answer is unknown.
    discovery_ids = {question["id"] for question in working_discovery.get("questions", []) if isinstance(question, Mapping)}
    if any(key not in discovery_ids for key in effective_answers):
        return _receipt("error", root, False, modules, [], ["answer_unknown"], fingerprint, _input_exit_code("answer_unknown"))
    try:
        actual_modules = normalize_skillsrc(proposed)["modules"]
    except (SkillsrcError, TypeError, ValueError, KeyError) as error:
        return _receipt("error", root, False, modules, [], [getattr(error, "code", "invalid_shape")], fingerprint, 2)
    if not write: return _receipt("preview", root, False, actual_modules, [], [], fingerprint)
    try:
        read_back = atomic_write_skillsrc(root, destination, proposed, fingerprint, original)
    except Exception as error:
        return _receipt("error", root, False, _modules_on_disk(destination), [], [getattr(error, "code", "write_error")], fingerprint, 1)
    return _receipt("created", root, True, actual_modules, [], [], fingerprint, read_back=read_back)


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
