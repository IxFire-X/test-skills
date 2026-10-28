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
import re
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


PYTEST = "pytest:selected-symbols-v1"
MAVEN = "maven-wrapper:selected-symbols-v1"
SYSTEM_MAVEN = "maven:selected-symbols-v1"
GRADLE = "gradle-wrapper:selected-symbols-v1"
ADAPTER_IDS = frozenset({PYTEST, MAVEN, SYSTEM_MAVEN, GRADLE})
SAFE_ENVIRONMENT_LABELS = frozenset({"PROJECT_NATIVE_ENV"})
_UNSAFE_TOKEN_CHARS = frozenset("|><;&$`\n\r")
# FaT-L: the project's style, license-header and static-analysis checks never change how a selected test behaves, so the
# selected-tests run skips this closed list (absent plugins ignore the property).  maven-enforcer is NOT here: it checks
# the environment.  Each property is the plugin's own user property:
MAVEN_STYLE_SKIPS = (
    "-Dcheckstyle.skip=true",                  # maven-checkstyle-plugin 3.6.0 plugin.xml; covers nohttp-checkstyle
    "-Dspring-javaformat.validate.skip=true",  # spring-javaformat-maven-plugin 0.0.47 plugin.xml (no spring-javaformat.skip)
    "-Dspotless.check.skip=true",              # spotless SpotlessCheckMojo: @Parameter(property = "spotless.check.skip")
    "-Drat.skip=true",                         # apache-rat-plugin check goal docs: User Property rat.skip
    "-Dlicense.skip=true",                     # com.mycila license-maven-plugin AbstractLicenseMojo: property = "license.skip"
    "-Dlicense.skipCheckLicense=true",         # org.codehaus.mojo license-maven-plugin check-file-header docs
    "-Dlicense.skipUpdateLicense=true",        # org.codehaus.mojo license-maven-plugin update-file-header docs
    "-Dformatter.skip=true",                   # net.revelc formatter-maven-plugin validate/format docs
    "-Dimpsort.skip=true",                     # net.revelc impsort-maven-plugin check/sort docs
    "-Dpmd.skip=true",                         # maven-pmd-plugin check goal docs
    "-Dcpd.skip=true",                         # maven-pmd-plugin cpd-check goal docs
    "-Dspotbugs.skip=true",                    # spotbugs-maven-plugin check goal docs
)


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
    from tools.project_inventory import InventoryError, module_runtime_path, system_maven_path

    if adapter_id == SYSTEM_MAVEN:
        try:
            return system_maven_path(test.get("executable"))
        except (InventoryError, OSError) as error:
            raise AdapterRequestError("system Maven is unavailable or unsafe") from error

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
    required = {"adapter_id", "build_profile", "adapter_parameters", "interpreter" if adapter_id == PYTEST else "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"}
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
    elif adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return ("target/surefire-reports",), (executable, "-B", f"-P{profile}", *MAVEN_STYLE_SKIPS, f"-Dtest={','.join(selectors)}", "test")
    elif adapter_id == GRADLE:
        # cleanTest reruns only the test task; --rerun-tasks rebuilt everything (FaT-L: 10 s against 34 s on Petclinic).
        # FaT-H2a: --no-build-cache — with org.gradle.caching the test task would come FROM-CACHE with old reports.
        gradle_selectors = tuple(selector.replace("#", ".", 1) for selector in selectors)
        return ("build/test-results/test",), (executable, "cleanTest", "test", "--no-build-cache", f"-Pprofile={profile}", *(part for selector in gradle_selectors for part in ("--tests", selector)))
    raise AdapterRequestError("adapter is not in the closed pilot set")


def probe_command_for(adapter_id: str, executable: str, profile: str) -> tuple[str, ...]:
    """FaT-L: the closed offline validate-phase probe of ``ready`` — the flags of the selected-tests run, no tests."""
    if adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return (executable, "-B", "-o", "-q", f"-P{profile}", *MAVEN_STYLE_SKIPS, "validate")
    raise AdapterRequestError("only Maven has a validate-phase probe")


def probe_executable(module_root: Path, test: Mapping[str, Any]) -> str:
    """The very runtime ``build_request`` would start, for the probe; NOT_RUNNABLE when it is unavailable."""
    adapter_id = test.get("adapter_id")
    runtime_key = "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"
    if adapter_id not in {MAVEN, SYSTEM_MAVEN}:
        raise AdapterRequestError("only Maven has a validate-phase probe")
    closed = {key: test.get(key) for key in ("adapter_id", "build_profile", "adapter_parameters", runtime_key)}
    root = Path(module_root).resolve()
    return str(_runtime(root, _test_config({"module_root": str(root), "test": closed}, str(adapter_id)), str(adapter_id)))


# FaT-L: the project's own style checks, read statically from its build files — for hints only, never for the command.
# name -> (Maven markers, Gradle markers, Maven format goal, Gradle format task, scans the whole project directory)
_STYLE_CHECKS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str | None, str | None, bool], ...] = (
    ("spring-javaformat", (r"<artifactId>\s*spring-javaformat-maven-plugin\s*</artifactId>",), (r"io\.spring\.javaformat",),
     "spring-javaformat:apply", "format", False),
    ("checkstyle", (r"<artifactId>\s*maven-checkstyle-plugin\s*</artifactId>",),
     (r"""(?:id|apply\s+plugin:)\s*\(?\s*['"]checkstyle['"]""",), None, None, False),
    ("nohttp", (r"<artifactId>\s*nohttp-checkstyle\s*</artifactId>",), (r"io\.spring\.nohttp",), None, None, True),
    ("spotless", (r"<artifactId>\s*spotless-maven-plugin\s*</artifactId>",), (r"com\.diffplug\.spotless",), "spotless:apply", "spotlessApply", False),
    ("formatter", (r"<artifactId>\s*formatter-maven-plugin\s*</artifactId>",), (), "formatter:format", None, False),
    ("impsort", (r"<artifactId>\s*impsort-maven-plugin\s*</artifactId>",), (), "impsort:sort", None, False),
    ("apache-rat", (r"<artifactId>\s*apache-rat-plugin\s*</artifactId>",), (), None, None, True),
    ("license-mycila", (r"<groupId>\s*com\.mycila\s*</groupId>\s*<artifactId>\s*license-maven-plugin",), (), "license:format", None, True),
    ("license-codehaus", (r"<groupId>\s*org\.codehaus\.mojo\s*</groupId>\s*<artifactId>\s*license-maven-plugin",), (),
     "license:update-file-header", None, False),
    ("pmd", (r"<artifactId>\s*maven-pmd-plugin\s*</artifactId>",), (), None, None, False),
    ("spotbugs", (r"<artifactId>\s*spotbugs-maven-plugin\s*</artifactId>",), (), None, None, False),
)
_XML_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_GRADLE_COMMENTS = re.compile(r"(?<![:\"'])//[^\n]*|/\*.*?\*/", re.S)


def _build_files(project_root: Path, module_root: Path) -> tuple[list[str], list[str]]:
    project, current = Path(project_root).resolve(), Path(module_root).resolve()
    maven: list[str] = []
    gradle: list[str] = []
    while True:
        for name, bucket in (("pom.xml", maven), ("build.gradle", gradle), ("build.gradle.kts", gradle),
                             ("settings.gradle", gradle), ("settings.gradle.kts", gradle)):
            path = current / name
            if path.is_file():
                comments = _XML_COMMENTS if bucket is maven else _GRADLE_COMMENTS
                try:
                    bucket.append(comments.sub(" ", path.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
        if current == project or project not in current.parents:
            return maven, gradle
        current = current.parent


def project_style_checks(project_root: Path, module_root: Path) -> tuple[str, ...]:
    """Names of the known style checks the module's build files (module up to the project root) declare."""
    maven, gradle = _build_files(project_root, module_root)
    return tuple(name for name, maven_markers, gradle_markers, *_rest in _STYLE_CHECKS
                 if any(re.search(marker, text) for marker in maven_markers for text in maven)
                 or any(re.search(marker, text) for marker in gradle_markers for text in gradle))


def whole_project_checks(checks: Sequence[str]) -> tuple[str, ...]:
    """The found checks that read the whole project directory, the package folder and .pilot-runs included."""
    return tuple(row[0] for row in _STYLE_CHECKS if row[5] and row[0] in checks)


def style_notes(adapter_id: str, executable: str, checks: Sequence[str], kept_files: Sequence[str]) -> dict[str, str]:
    """FaT-L: what the selected-tests run skipped and how to format the generated files left in the project."""
    notes: dict[str, str] = {}
    if adapter_id in {MAVEN, SYSTEM_MAVEN}:
        found = ", ".join(checks) if checks else (
            "в сборке проекта не найдены; флаги пропуска переданы всегда: "
            + ", ".join(flag.removeprefix("-D").removesuffix("=true") for flag in MAVEN_STYLE_SKIPS))
        notes["style_checks_skipped"] = f"проверки стиля проекта при запуске тестов пакета пропущены: {found}"
    if adapter_id not in {MAVEN, SYSTEM_MAVEN, GRADLE} or not checks or not kept_files:
        return notes
    rows = [row for row in _STYLE_CHECKS if row[0] in checks]
    # FaT-H2a: only spotless (Maven) takes a file filter; every other command formats the whole module.
    spotless_files = ",".join(".*" + r"[\\/]".join(re.escape(part) for part in path.split("/")) for path in kept_files)
    if adapter_id == GRADLE:
        commands = [f'"{executable}" {row[4]}' for row in rows if row[4]]
        whole = bool(commands)
        check = None
    else:
        commands = [f'"{executable}" {row[3]}' + (f' -DspotlessFiles="{spotless_files}"' if row[0] == "spotless" else "")
                    for row in rows if row[3]]
        whole = any(row[3] and row[0] != "spotless" for row in rows)
        check = f'"{executable}" -B validate'
    if not commands and check is None:
        return notes
    scope = ("команда форматирует весь модуль, не только эти файлы — проверьте дифф чужих файлов перед коммитом"
             if whole else "команда форматирует только эти файлы")
    action = "; ".join(filter(None, [(f"отформатируйте ({scope}): " + "; ".join(commands)) if commands else None,
                                     f"затем проверьте: {check}" if check else None]))
    notes["format_hint"] = (f"оставленные в проекте файлы ({', '.join(kept_files)}) проект проверяет на стиль "
                            f"({', '.join(checks)}): {action}")
    return notes


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
