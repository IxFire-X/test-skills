"""Closed, project-native execution request builders and result classification.

This seam builds tokenized requests only.  It has no shell-template interface and it
does not install dependencies or decide retry policy.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


PYTEST = "pytest:selected-symbols-v1"
MAVEN = "maven-wrapper:selected-symbols-v1"
GRADLE = "gradle-wrapper:selected-symbols-v1"
ADAPTER_IDS = frozenset({PYTEST, MAVEN, GRADLE})
SAFE_ENVIRONMENT_LABELS = frozenset({"PROJECT_NATIVE_ENV"})
_UNSAFE_TOKEN_CHARS = frozenset("|><;&$`\n\r")


class AdapterRequestError(ValueError):
    """A request cannot safely start and is therefore NOT_RUNNABLE."""

    code = "NOT_RUNNABLE"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class ExecutionRequest:
    adapter_id: str
    executable: str
    argv: tuple[str, ...]
    cwd: str
    selectors: tuple[str, ...]
    timeout_seconds: int
    report_paths: tuple[str, ...]
    environment_labels: tuple[str, ...]
    build_profile: str = ""
    typed_parameters: tuple[tuple[str, str], ...] = ()


def request_digest(request: ExecutionRequest) -> str:
    """Digest every process-significant closed request fact for an attempt gate."""
    if not isinstance(request, ExecutionRequest):
        raise AdapterRequestError("request is not an execution request")
    body = {
        "adapter_id": request.adapter_id,
        "executable": request.executable,
        "argv": list(request.argv),
        "cwd": request.cwd,
        "selectors": list(request.selectors),
        "timeout_seconds": request.timeout_seconds,
        "report_paths": list(request.report_paths),
        "environment_labels": list(request.environment_labels),
        "build_profile": request.build_profile,
        "typed_parameters": list(request.typed_parameters),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AdapterRequestError(f"{label} is missing")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise AdapterRequestError(f"{label} must be module-relative")
    return value


def _runtime(module_root: Path, test: Mapping[str, Any], adapter_id: str) -> Path:
    from tools.project_inventory import InventoryError, module_runtime_path

    key = "interpreter" if adapter_id == PYTEST else "wrapper"
    relative = _safe_relative(test.get(key), key)
    try:
        runtime = module_runtime_path(module_root, relative)
    except (InventoryError, OSError) as error:
        raise AdapterRequestError(f"module-local {key} is unavailable or unsafe") from error
    if adapter_id != PYTEST and runtime.is_symlink():
        raise AdapterRequestError("wrapper must be a regular module-local file")
    if os.name != "nt" and not os.access(runtime, os.X_OK):
        raise AdapterRequestError(f"module-local {key} is not executable")
    return runtime


def _selectors(reviewed_targets: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for target in reviewed_targets:
        if not isinstance(target, Mapping) or set(target) != {"selector"}:
            raise AdapterRequestError("reviewed targets must contain only exact selectors")
        selector = target["selector"]
        if not isinstance(selector, str) or not selector or any(char in _UNSAFE_TOKEN_CHARS for char in selector):
            raise AdapterRequestError("reviewed selector is unsafe")
        if selector not in values:
            values.append(selector)
    if not values:
        raise AdapterRequestError("reviewed selector set is empty")
    return tuple(values)


def _module_root(module: Mapping[str, Any]) -> Path:
    root = module.get("module_root", module.get("root"))
    if not isinstance(root, str) or not root:
        raise AdapterRequestError("exact module root is unavailable")
    value = Path(root).resolve()
    if not value.is_dir():
        raise AdapterRequestError("exact module root is unavailable")
    return value


def _test_config(module: Mapping[str, Any], adapter_id: str) -> Mapping[str, Any]:
    test = module.get("test")
    if not isinstance(test, Mapping) or test.get("adapter_id") != adapter_id:
        raise AdapterRequestError("selected module is not bound to requested adapter")
    required = {"adapter_id", "build_profile", "adapter_parameters", "interpreter" if adapter_id == PYTEST else "wrapper"}
    if set(test) != required:
        raise AdapterRequestError("test configuration is not a closed adapter configuration")
    profile, parameters = test.get("build_profile"), test.get("adapter_parameters")
    if not isinstance(profile, str) or not profile or any(char in _UNSAFE_TOKEN_CHARS for char in profile):
        raise AdapterRequestError("build profile is invalid")
    if not isinstance(parameters, Mapping) or parameters:
        raise AdapterRequestError("adapter parameters must be the closed empty tuple for pilot")
    return test


def build_request(adapter_id: str, module: Mapping[str, Any], reviewed_targets: Sequence[Mapping[str, Any]]) -> ExecutionRequest:
    """Build an exact argv vector for one selected module, or fail before start."""
    if adapter_id not in ADAPTER_IDS:
        raise AdapterRequestError("adapter is not in the closed pilot set")
    root, test, selectors = _module_root(module), _test_config(module, adapter_id), _selectors(reviewed_targets)
    runtime, profile = _runtime(root, test, adapter_id), test["build_profile"]
    parameters = tuple(sorted((str(key), str(value)) for key, value in test["adapter_parameters"].items()))
    reports, argv = command_for(adapter_id, str(runtime), profile, selectors)
    return ExecutionRequest(adapter_id, str(runtime), argv, str(root), selectors, 600, reports, ("PROJECT_NATIVE_ENV",), profile, parameters)


def command_for(adapter_id: str, executable: str, profile: str, selectors: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """One closed command policy for construction, recovery, and receipt validation."""
    if adapter_id == PYTEST:
        return ("test-results/pytest.xml",), (executable, "-m", "pytest", "--junitxml", "test-results/pytest.xml", *selectors)
    elif adapter_id == MAVEN:
        return ("target/surefire-reports",), (executable, f"-P{profile}", f"-Dtest={','.join(selectors)}", "test")
    elif adapter_id == GRADLE:
        gradle_selectors = tuple(selector.replace("#", ".", 1) for selector in selectors)
        return ("build/test-results/test",), (executable, "test", "--rerun-tasks", f"-Pprofile={profile}", *(part for selector in gradle_selectors for part in ("--tests", selector)))
    raise AdapterRequestError("adapter is not in the closed pilot set")


def classify_prestart(module: Mapping[str, Any]) -> Literal["READY", "NOT_RUNNABLE"]:
    """Conservative prestart classification without launching a project process."""
    try:
        test = module.get("test")
        if not isinstance(test, Mapping):
            return "NOT_RUNNABLE"
        adapter = test.get("adapter_id")
        _runtime(_module_root(module), _test_config(module, adapter), adapter)
    except (AdapterRequestError, TypeError):
        return "NOT_RUNNABLE"
    return "READY"


def invoke_request(request: ExecutionRequest, runner: Any) -> Any:
    """Invoke an injected process primitive with the only permitted launch shape.

    Environment values are intentionally not part of ``ExecutionRequest``; a later
    controller may hold opaque runtime handles, but it cannot smuggle them through
    this adapter API or turn argv into a shell string.
    """
    return runner(request.argv, cwd=request.cwd, timeout=request.timeout_seconds, shell=False)


def _case_selector(case: ET.Element) -> tuple[str, ...]:
    classname, name = case.attrib.get("classname", ""), case.attrib.get("name", "")
    if not classname:
        return ("", f"#{name}")
    parts = classname.split(".")
    python_function = classname.replace(".", "/") + ".py::" + name
    values = [python_function, f"{classname}#{name}"]
    if len(parts) >= 2:
        module, class_name = ".".join(parts[:-1]), parts[-1]
        values.append(module.replace(".", "/") + f".py::{class_name}::{name}")
    return tuple(values)


def _declared_test_count(root: ET.Element) -> int | None:
    """Return the report's declared test count, or None if the document is not process-bound."""
    declared = root.get("tests")
    if declared is not None:
        try:
            value = int(declared)
        except ValueError:
            return None
        return value if value >= 0 else None
    if root.tag == "testsuites":
        total = 0
        found = False
        for suite in root.findall("testsuite"):
            attr = suite.get("tests")
            if attr is None:
                continue
            try:
                value = int(attr)
            except ValueError:
                return None
            if value < 0:
                return None
            total += value
            found = True
        return total if found else None
    return None


def _is_zero_test_root(root: ET.Element) -> bool:
    return (
        root.tag in {"testsuite", "testsuites"}
        and _declared_test_count(root) == 0
        and not list(root.iter("testcase"))
    )


def is_zero_test_report(report_bytes: bytes | None) -> bool:
    """Return whether a valid framework report proves zero collected test cases."""
    if not report_bytes:
        return False
    try:
        return _is_zero_test_root(ET.fromstring(report_bytes))
    except ET.ParseError:
        return False


def classify_execution(*, controller_timed_out: bool, process_returncode: int | None, report_bytes: bytes | None, selectors: Sequence[str]) -> Literal["PASS", "FAIL", "UNKNOWN"]:
    """Give authoritative exact framework evidence priority over controller timeout."""
    if process_returncode is not None and process_returncode < 0:
        return "UNKNOWN"
    if not report_bytes:
        return "UNKNOWN"
    try:
        root = ET.fromstring(report_bytes)
        cases = list(root.iter("testcase"))
    except ET.ParseError:
        return "UNKNOWN"
    if _is_zero_test_root(root):
        if controller_timed_out or process_returncode is None:
            return "UNKNOWN"
        return "FAIL"
    expected = set(selectors)
    matched: dict[str, ET.Element] = {}
    for case in cases:
        for name in _case_selector(case):
            if name in expected:
                if name in matched:
                    return "UNKNOWN"
                matched[name] = case
    if set(matched) != expected:
        return "UNKNOWN"
    for case in matched.values():
        if case.find("failure") is not None or case.find("error") is not None:
            return "FAIL"
        if case.find("skipped") is not None:
            return "UNKNOWN"
    # A framework-reported exact failure is authoritative; a clean report is not
    # sufficient after controller termination because process completion is unknown.
    if controller_timed_out:
        return "UNKNOWN"
    return "PASS"


def unknown_allows_automatic_retry(verification: str) -> bool:
    """The frozen contract categorically forbids automatic UNKNOWN retry."""
    return verification != "UNKNOWN"
