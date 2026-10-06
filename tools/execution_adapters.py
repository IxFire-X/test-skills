"""Closed, project-native execution request builders and result classification.

This seam builds tokenized requests only.  It has no shell-template interface and it
does not install dependencies or decide retry policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


PYTEST = "pytest:selected-symbols-v1"
MAVEN = "maven-wrapper:selected-symbols-v1"
SYSTEM_MAVEN = "maven:selected-symbols-v1"
GRADLE = "gradle-wrapper:selected-symbols-v1"
ADAPTER_IDS = frozenset({PYTEST, MAVEN, SYSTEM_MAVEN, GRADLE})
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


DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 86400
# ``build_profile`` is a required manifest field; this value means "no profile declared".
UNDECLARED_PROFILE = "default"
_OPTIONAL_TEST_KEYS = frozenset({"build_root", "timeout_seconds"})
_REPORT_DIRECTORIES = {
    PYTEST: "test-results/pytest.xml",
    MAVEN: "target/surefire-reports",
    SYSTEM_MAVEN: "target/surefire-reports",
    GRADLE: "build/test-results/test",
}
_BUILD_ROOT_MARKERS = {
    MAVEN: ("pom.xml",),
    SYSTEM_MAVEN: ("pom.xml",),
    GRADLE: ("settings.gradle", "settings.gradle.kts"),
}
_WRAPPER_NAMES = {"mvnw": ("mvnw", "mvnw.cmd"), "mvnw.cmd": ("mvnw", "mvnw.cmd"), "gradlew": ("gradlew", "gradlew.bat"), "gradlew.bat": ("gradlew", "gradlew.bat")}
_MODULE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
_VENV_POSIX = re.compile(r"(?P<venv>(?:[^/]+/)*)bin/python(?:[0-9]+(?:\.[0-9]+)*)?")
_VENV_WINDOWS = re.compile(r"(?P<venv>(?:[^/]+/)*)Scripts/python\.exe", re.IGNORECASE)


@dataclass(frozen=True)
class LaunchLayout:
    """Where one module's build tool starts and how the module is named there."""

    build_root: Path
    module_path: str
    timeout_seconds: int


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AdapterRequestError(f"{label} is missing")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise AdapterRequestError(f"{label} must be module-relative")
    return value


def timeout_is_closed(value: Any) -> bool:
    """A launch timeout is a positive whole number of seconds within one day."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_TIMEOUT_SECONDS


def runtime_candidates(adapter_id: str, value: str, *, os_name: str | None = None) -> tuple[str, ...]:
    """Map a logical or legacy ``.skillsrc`` runtime name to host-specific relative paths.

    ``python`` names the module's own virtual environment; ``mvnw``/``gradlew`` name
    the wrapper launcher for the current host.  A legacy OS-specific path is tried
    literally first, then as its counterpart on the other host family.
    """
    windows = (os.name if os_name is None else os_name) == "nt"
    text = value.replace("\\", "/")
    if adapter_id == PYTEST:
        if text == "python":
            return tuple(f"{venv}/Scripts/python.exe" if windows else f"{venv}/bin/python" for venv in (".venv", "venv"))
        posix, win = _VENV_POSIX.fullmatch(text), _VENV_WINDOWS.fullmatch(text)
        if posix and windows:
            return (text, f"{posix['venv']}Scripts/python.exe")
        if win and not windows:
            return (text, f"{win['venv']}bin/python")
        return (text,)
    if adapter_id in {MAVEN, GRADLE}:
        head, _, name = text.rpartition("/")
        names = _WRAPPER_NAMES.get(name)
        if names is None:
            return (text,)
        return ((head + "/" if head else "") + names[1 if windows else 0],)
    return (text,)


def _resolve_runtime(base: Path, test: Mapping[str, Any], adapter_id: str, *, require_executable: bool = True) -> Path:
    from tools.project_inventory import InventoryError, module_runtime_path, system_maven_path

    if adapter_id == SYSTEM_MAVEN:
        try:
            return system_maven_path(test.get("executable"))
        except (InventoryError, OSError) as error:
            raise AdapterRequestError("system Maven is unavailable or unsafe") from error

    key = "interpreter" if adapter_id == PYTEST else "wrapper"
    relative = _safe_relative(test.get(key), key)
    failure: Exception | None = None
    runtime: Path | None = None
    for candidate in runtime_candidates(adapter_id, relative):
        try:
            runtime = module_runtime_path(base, candidate)
            break
        except (InventoryError, OSError) as error:
            failure = error
    if runtime is None:
        raise AdapterRequestError(f"module-local {key} is unavailable or unsafe") from failure
    if adapter_id != PYTEST and runtime.is_symlink():
        raise AdapterRequestError("wrapper must be a regular module-local file")
    if require_executable and os.name != "nt" and not os.access(runtime, os.X_OK):
        raise AdapterRequestError(f"module-local {key} is not executable")
    return runtime


def _runtime(module_root: Path, test: Mapping[str, Any], adapter_id: str) -> Path:
    return _resolve_runtime(module_root, test, adapter_id)


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
    required = {"adapter_id", "build_profile", "adapter_parameters", "interpreter" if adapter_id == PYTEST else "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"}
    optional = {"timeout_seconds"} if adapter_id == PYTEST else _OPTIONAL_TEST_KEYS
    if not required <= set(test) or set(test) - required - optional:
        raise AdapterRequestError("test configuration is not a closed adapter configuration")
    profile, parameters = test.get("build_profile"), test.get("adapter_parameters")
    if not isinstance(profile, str) or not profile or any(char in _UNSAFE_TOKEN_CHARS for char in profile):
        raise AdapterRequestError("build profile is invalid")
    if not isinstance(parameters, Mapping) or parameters:
        raise AdapterRequestError("adapter parameters must be the closed empty tuple for pilot")
    if "timeout_seconds" in test and not timeout_is_closed(test["timeout_seconds"]):
        raise AdapterRequestError("timeout_seconds must be a whole number of seconds from 1 to 86400")
    return test


def _safe_module_path(value: str) -> str:
    if value and not all(_MODULE_SEGMENT.fullmatch(part) and part not in {".", ".."} for part in value.split("/")):
        raise AdapterRequestError("module path inside the build root is not a portable relative path")
    return value


def launch_layout(module: Mapping[str, Any]) -> LaunchLayout:
    """Resolve the build root, the module's path inside it, and the launch timeout.

    ``test.build_root`` is project-relative (``.`` is the project root) and must be the
    module root or one of its ancestors.  Without it the module is its own build root.
    """
    root = _module_root(module)
    test = module.get("test")
    if not isinstance(test, Mapping):
        raise AdapterRequestError("selected module has no test configuration")
    timeout = test.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not timeout_is_closed(timeout):
        raise AdapterRequestError("timeout_seconds must be a whole number of seconds from 1 to 86400")
    declared = test.get("build_root")
    if declared is None:
        return LaunchLayout(root, "", timeout)
    adapter_id = test.get("adapter_id")
    if adapter_id not in _BUILD_ROOT_MARKERS:
        raise AdapterRequestError("build_root applies only to Maven and Gradle modules")
    relative_root = module.get("root")
    if not isinstance(declared, str) or not isinstance(relative_root, str) or not relative_root:
        raise AdapterRequestError("build_root requires the project-relative module root")
    module_parts = [part for part in relative_root.replace("\\", "/").split("/") if part not in {"", "."}]
    build_parts = [part for part in _safe_relative(declared, "build_root").replace("\\", "/").split("/") if part not in {"", "."}]
    actual_tail = [os.path.normcase(part) for part in root.parts[len(root.parts) - len(module_parts):]]
    if module_parts[:len(build_parts)] != build_parts or actual_tail != [os.path.normcase(part) for part in module_parts]:
        raise AdapterRequestError("build_root must be the module root or one of its ancestors")
    module_path = _safe_module_path("/".join(module_parts[len(build_parts):]))
    build_root = root
    for _part in module_path.split("/") if module_path else ():
        build_root = build_root.parent
    if not any((build_root / marker).is_file() for marker in _BUILD_ROOT_MARKERS[adapter_id]):
        raise AdapterRequestError("build_root does not contain the build's root manifest")
    return LaunchLayout(build_root, module_path, timeout)


def resolve_module_runtime(module: Mapping[str, Any]) -> tuple[Path, str]:
    """Return the directory a module-local runtime is bound to and its concrete relative path.

    Wrappers live in the build root; the interpreter lives in the module's own venv.
    """
    test = module.get("test")
    adapter_id = test.get("adapter_id") if isinstance(test, Mapping) else None
    if adapter_id not in {PYTEST, MAVEN, GRADLE}:
        raise AdapterRequestError("adapter has no module-local runtime")
    base = launch_layout(module).build_root
    runtime = _resolve_runtime(base, test, adapter_id, require_executable=False)
    return base, runtime.relative_to(base).as_posix()


def _build(adapter_id: str, module: Mapping[str, Any], reviewed_targets: Sequence[Mapping[str, Any]], *, gate: bool) -> ExecutionRequest:
    if adapter_id not in ADAPTER_IDS:
        raise AdapterRequestError("adapter is not in the closed pilot set")
    test, selectors = _test_config(module, adapter_id), _selectors(reviewed_targets)
    layout = launch_layout(module)
    runtime, profile = _resolve_runtime(layout.build_root, test, adapter_id), test["build_profile"]
    parameters = tuple(sorted((str(key), str(value)) for key, value in test["adapter_parameters"].items()))
    if gate:
        reports, argv = (), gate_command_for(adapter_id, str(runtime), profile, selectors, module_path=layout.module_path)
    else:
        reports, argv = command_for(adapter_id, str(runtime), profile, selectors, module_path=layout.module_path)
    return ExecutionRequest(adapter_id, str(runtime), argv, str(layout.build_root), selectors, layout.timeout_seconds, reports, ("PROJECT_NATIVE_ENV",), profile, parameters)


def build_request(adapter_id: str, module: Mapping[str, Any], reviewed_targets: Sequence[Mapping[str, Any]]) -> ExecutionRequest:
    """Build an exact argv vector for one selected module, or fail before start."""
    return _build(adapter_id, module, reviewed_targets, gate=False)


def build_gate_command(adapter_id: str, module: Mapping[str, Any], reviewed_targets: Sequence[Mapping[str, Any]]) -> ExecutionRequest:
    """Build the compile/collect-only request for the same module and selectors.

    It takes the same inputs as :func:`build_request` and returns the same request
    shape (argv, cwd, timeout); ``report_paths`` is empty because the gate produces
    no framework report.  pytest collects the selected node IDs, Maven compiles
    tests up to ``test-compile``, Gradle builds ``testClasses``.
    """
    return _build(adapter_id, module, reviewed_targets, gate=True)


def _maven_scope(profile: str, module_path: str) -> tuple[str, ...]:
    declared = () if profile == UNDECLARED_PROFILE else (f"-P{profile}",)
    return (*declared, "-pl", module_path, "-am") if module_path else declared


def _gradle_task(module_path: str, task: str) -> str:
    return ":" + ":".join([*(module_path.split("/") if module_path else ()), task])


def _gradle_profile(profile: str) -> tuple[str, ...]:
    return () if profile == UNDECLARED_PROFILE else (f"-Pprofile={profile}",)


def command_for(adapter_id: str, executable: str, profile: str, selectors: Sequence[str], *, module_path: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """One closed command policy for construction, recovery, and receipt validation.

    ``module_path`` is the module's portable path inside the build root (the launch
    cwd); it is empty when the module is its own build root.  Report paths stay
    relative to the module root.
    """
    module_path = _safe_module_path(module_path)
    if adapter_id == PYTEST:
        if module_path:
            raise AdapterRequestError("pytest runs from the module root")
        return (_REPORT_DIRECTORIES[PYTEST],), (executable, "-m", "pytest", "--junitxml", _REPORT_DIRECTORIES[PYTEST], *selectors)
    elif adapter_id in {MAVEN, SYSTEM_MAVEN}:
        reactor = ("-Dsurefire.failIfNoSpecifiedTests=false",) if module_path else ()
        return (_REPORT_DIRECTORIES[adapter_id],), (executable, *_maven_scope(profile, module_path), f"-Dtest={','.join(selectors)}", *reactor, "-B", "-ntp", "test")
    elif adapter_id == GRADLE:
        gradle_selectors = tuple(selector.replace("#", ".", 1) for selector in selectors)
        # ``cleanTest`` forces only the test task to rerun and exists in every Gradle
        # version; ``--rerun`` needs 7.6+ and ``--rerun-tasks`` rebuilds the whole graph.
        return (_REPORT_DIRECTORIES[GRADLE],), (
            executable, _gradle_task(module_path, "cleanTest"), _gradle_task(module_path, "test"),
            *(part for selector in gradle_selectors for part in ("--tests", selector)),
            *_gradle_profile(profile), "--no-daemon",
        )
    raise AdapterRequestError("adapter is not in the closed pilot set")


def gate_command_for(adapter_id: str, executable: str, profile: str, selectors: Sequence[str], *, module_path: str = "") -> tuple[str, ...]:
    """Closed compile/collect-only argv that runs no test body."""
    module_path = _safe_module_path(module_path)
    if adapter_id == PYTEST:
        if module_path:
            raise AdapterRequestError("pytest runs from the module root")
        return (executable, "-m", "pytest", "--collect-only", "-q", *selectors)
    if adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return (executable, *_maven_scope(profile, module_path), "-B", "-ntp", "test-compile")
    if adapter_id == GRADLE:
        return (executable, _gradle_task(module_path, "testClasses"), *_gradle_profile(profile), "--no-daemon")
    raise AdapterRequestError("adapter is not in the closed pilot set")


def request_module_path(request: ExecutionRequest) -> str:
    """Recover the module's path inside the launch cwd from the closed argv."""
    argv = request.argv
    if request.adapter_id in {MAVEN, SYSTEM_MAVEN} and "-pl" in argv:
        index = argv.index("-pl")
        return _safe_module_path(argv[index + 1]) if index + 1 < len(argv) else ""
    if request.adapter_id == GRADLE and len(argv) > 1 and argv[1].startswith(":"):
        for task in ("cleanTest", "testClasses"):
            if argv[1].endswith(":" + task):
                return _safe_module_path(argv[1][1:-len(task)].rstrip(":").replace(":", "/"))
    return ""


def request_module_root(request: ExecutionRequest) -> Path:
    """The selected module's directory: the launch cwd, or the module inside the build root."""
    cwd = Path(request.cwd)
    module_path = request_module_path(request)
    return cwd.joinpath(*module_path.split("/")) if module_path else cwd


def _legacy_command_for(adapter_id: str, executable: str, profile: str, selectors: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """The module-root command shape recorded by packs before the multi-module fix."""
    if adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return (_REPORT_DIRECTORIES[adapter_id],), (executable, f"-P{profile}", f"-Dtest={','.join(selectors)}", "test")
    if adapter_id == GRADLE:
        gradle_selectors = tuple(selector.replace("#", ".", 1) for selector in selectors)
        return (_REPORT_DIRECTORIES[GRADLE],), (executable, "test", "--rerun-tasks", f"-Pprofile={profile}", *(part for selector in gradle_selectors for part in ("--tests", selector)))
    return None


def command_for_request(request: ExecutionRequest) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Recompute the one closed command a recorded request is allowed to carry.

    New requests always use :func:`command_for`.  A receipt recorded by an older
    pack keeps verifying against the exact legacy shape it was launched with.
    """
    legacy = _legacy_command_for(request.adapter_id, request.executable, request.build_profile, request.selectors)
    if legacy is not None and request.argv == legacy[1]:
        return legacy
    return command_for(request.adapter_id, request.executable, request.build_profile, request.selectors, module_path=request_module_path(request))


def request_scope_is_closed(request: ExecutionRequest, project: Path, module: Path) -> bool:
    """True when the launch cwd is ``module`` or its declared build root inside ``project``.

    For a multi-module build the cwd is an ancestor of the module that holds the
    build's root manifest; the module is then named inside argv.
    """
    try:
        if not isinstance(request, ExecutionRequest):
            return False
        project, module, cwd = Path(project).resolve(), Path(module).resolve(), Path(request.cwd).resolve()
        module_path = request_module_path(request)
        if request_module_root(request).resolve() != module or not module.is_relative_to(project) or not cwd.is_relative_to(project):
            return False
        return not module_path or any((cwd / marker).is_file() for marker in _BUILD_ROOT_MARKERS.get(request.adapter_id, ()))
    except (AdapterRequestError, OSError, TypeError, ValueError):
        return False


def request_launch_is_closed(request: ExecutionRequest, project: Path, module: Path) -> bool:
    """True when cwd, timeout, argv and reports are exactly the policy for ``module``."""
    if not request_scope_is_closed(request, project, module) or not timeout_is_closed(request.timeout_seconds):
        return False
    try:
        reports, argv = command_for_request(request)
    except (AdapterRequestError, TypeError, ValueError):
        return False
    return request.argv == argv and request.report_paths == reports


def classify_prestart(module: Mapping[str, Any]) -> Literal["READY", "NOT_RUNNABLE"]:
    """Conservative prestart classification without launching a project process."""
    try:
        test = module.get("test")
        if not isinstance(test, Mapping):
            return "NOT_RUNNABLE"
        adapter = test.get("adapter_id")
        _resolve_runtime(launch_layout(module).build_root, _test_config(module, adapter), adapter)
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
