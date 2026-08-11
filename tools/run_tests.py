#!/usr/bin/env python3
"""
run_tests.py — детерминированный оракул исполнения (Опора 1, BACKLOG.md).

Единственный источник правды о том, запустились ли автотесты.
В отличие от LLM-вердиктов (которые могут лгать AUTO_FIX_APPLIED), этот
вердикт ставится по факту запуска процесса раннера и парсинга его вывода.

Принципы:
  - PASS = тесты РЕАЛЬНО запустились и прошли.
  - FAIL = запустились, но есть падения (ассерты / ошибки).
  - NOT_RUNNABLE = окружение недоступно. НИКОГДА не подменяется на PASS.
    Именно это ловит ложный AUTO_FIX_APPLIED на коде, который физически не запускался.
  - Зависимости: только стандартная библиотека Python. Ничего ставить не нужно.
  - Вывод: JSON по контракту schemas/run-tests-output.schema.json в stdout.

Использование:
    python tools/run_tests.py --project /path/to/inventree --language python
    python tools/run_tests.py --project . --language python --pytest-target tests/test_part_api.py
    python tools/run_tests.py --skillsrc .skillsrc           # стек из манифеста

Жёсткий gate в Оркестраторе (BACKLOG Опора 1, п.4):
    autotest-reviewer НЕ выдаёт ПРИНЯТО, пока run_tests.py не вернул PASS.
    NOT_RUNNABLE — это ЧЕСТНЫЙ ответ «не могу проверить», а не ПРИНЯТО.

Выходные exit codes (для встраивания в CI):
    0 — PASS (тесты прошли)
    1 — FAIL (тесты упали)
    2 — NOT_RUNNABLE или ошибка самого раннера (некорректный вызов)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

if __package__:
    from .json_cli import JsonArgumentParser
    from .skillsrc_manifest import (
        SkillsrcError,
        load_skillsrc,
        normalize_skillsrc,
        resolve_module_root,
        select_module,
    )
else:  # direct CLI execution
    from json_cli import JsonArgumentParser
    from skillsrc_manifest import (
        SkillsrcError,
        load_skillsrc,
        normalize_skillsrc,
        resolve_module_root,
        select_module,
    )

# ---------------------------------------------------------------------------
# Конфигурация стека
# ---------------------------------------------------------------------------

# language → (test framework, нативный раннер, способ проверки наличия)
PYTHON_STACK = {
    "framework": "pytest",
    "runner": "pytest",
    "interpreters": ["python3", "python"],
}

JAVA_STACK = {
    "framework": "junit5",
    "runner_maven": "maven",
    "runner_gradle": "gradle",
}

JS_STACK = {
    "framework": "jest",
    "runners": ["npm", "yarn"],
}

GO_STACK = {
    "framework": "go-testing",
    "runner": "go",
}

MAX_OUTPUT_EXCERPT = 60  # строк сырого вывода в raw_output_excerpt
COMMAND_TIMEOUT = 600     # секунд на весь запуск раннера


def is_windows() -> bool:
    """Return the host command-wrapper convention without exposing global state to tests."""
    return os.name == "nt"


def resolve_execution_context(
    project_root: Path,
    skillsrc_path: Path,
    module_id: str | None,
    language_override: str | None,
) -> tuple[Path, str, dict[str, Any]]:
    """Resolve a selected manifest module into its confined execution context."""
    document = normalize_skillsrc(load_skillsrc(skillsrc_path))
    module = select_module(document, module_id)
    execution_root = resolve_module_root(project_root, module)
    detected_language = module["stack"]["language"]
    if language_override and language_override != detected_language:
        raise SkillsrcError("language_conflict", "--language conflicts with selected module")
    return execution_root, language_override or detected_language, module


# ---------------------------------------------------------------------------
# Проверка окружения
# ---------------------------------------------------------------------------

def select_python_interpreter(project_dir: str, override: str | None) -> str | None:
    """Select the project's execution interpreter without probing a different one."""
    root = Path(project_dir)
    candidates: list[str | Path | None] = [override]
    candidates.extend([root / ".venv/Scripts/python.exe", root / ".venv/bin/python"])
    candidates.append(Path(sys.executable))
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)


def check_python_env(project_dir: str, python_executable: str | None = None) -> dict:
    """
    Проверяет доступность python + pytest для проекта.
    Возвращает environment-блок контракта.
    """
    missing = []
    interpreter = None
    interpreter_bin = select_python_interpreter(project_dir, python_executable)

    def _probe(binary: str) -> str | None:
        """Возвращает версию интерпретатора, если binary реально запускается.

        Store-заглушка Windows (WindowsApps\\python3.exe) возвращает rc=9009 и
        текст 'Python' — отсеиваем её: считаем интерпретатор рабочим только при
        rc==0 и наличии цифры версии в выводе.
        """
        try:
            ver = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if ver.returncode != 0:
            return None
        out = (ver.stdout or ver.stderr or "").strip()
        # настоящий интерпретатор печатает 'Python X.Y.Z' (с цифрой версии)
        if not re.search(r"\d+\.\d+", out):
            return None
        return out or binary

    # Do not fall back after selection: probing and execution must use precisely
    # the same project/override interpreter so the verdict is reproducible.
    if interpreter_bin:
        interpreter = _probe(interpreter_bin)


    if not interpreter_bin or not interpreter:
        missing.append("python")

    # pytest: проверяем через -c "import pytest"
    pytest_available = False
    if interpreter_bin and interpreter:
        try:
            r = subprocess.run(
                [interpreter_bin, "-c", "import pytest; print(pytest.__version__)"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if r.returncode == 0:
                pytest_available = True
        except (subprocess.SubprocessError, OSError):
            pass

    if not pytest_available:
        missing.append("pytest")

    # проект директория существует?
    if not os.path.isdir(project_dir):
        missing.append("project_dir")

    if missing:
        status = "missing" if len(missing) >= 2 and "python" in missing else "partial"
        if not interpreter_bin:
            status = "missing"
    else:
        status = "ready"

    return {
        "status": status,
        "interpreter": interpreter,
        "working_dir": os.path.abspath(project_dir),
        "missing": missing or None,
        "interpreter_path": interpreter_bin,
        "_interpreter_bin": interpreter_bin if interpreter else None,
    }


def check_java_env(project_dir: str) -> dict:
    missing = []
    interpreter = None

    # java
    java_home = os.environ.get("JAVA_HOME")
    java_names = ["java.exe", "java"] if os.name == "nt" else ["java"]
    java_bin = next(
        (str(Path(java_home) / "bin" / name) for name in java_names
         if java_home and (Path(java_home) / "bin" / name).is_file()),
        None,
    ) or shutil.which("java")
    if java_bin:
        try:
            ver = subprocess.run([java_bin, "-version"], capture_output=True, text=True, timeout=10, check=False)
            interpreter = (ver.stderr or ver.stdout).strip().splitlines()[0] if (ver.stderr or ver.stdout) else "java"
        except (subprocess.SubprocessError, OSError):
            interpreter = "java"
    else:
        missing.append("java")

    # maven или gradle
    runner = None
    wrapper_candidates = (["mvnw.cmd", "mvnw", "gradlew.cmd", "gradlew.bat", "gradlew"]
                          if os.name == "nt" else ["mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"])
    for wrapper in wrapper_candidates:
        if os.path.isfile(os.path.join(project_dir, wrapper)):
            runner = wrapper
            break
    if not runner:
        if shutil.which("mvn"):
            runner = "mvn"
        elif shutil.which("gradle"):
            runner = "gradle"
        else:
            missing.append("maven/gradle")

    if not os.path.isdir(project_dir):
        missing.append("project_dir")

    status = "ready" if not missing else ("missing" if not java_bin else "partial")
    return {
        "status": status,
        "interpreter": interpreter,
        "working_dir": os.path.abspath(project_dir),
        "missing": missing or None,
        "_runner": runner,
    }


# ---------------------------------------------------------------------------
# Запуск раннеров и парсинг вывода
# ---------------------------------------------------------------------------

def run_subprocess(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Запускает процесс, возвращает (exit_code, stdout, stderr)."""
    # Windows: Python (CreateProcess) не умеет напрямую запускать .cmd/.bat —
    # это ограничение WinAPI. Если первый элемент команды резолвится в .cmd/.bat,
    # оборачиваем в `cmd /c`, тогда Maven/Gradle wrapper'ы работают.
    if os.name == "nt" and cmd:
        first = cmd[0]
        local_candidate = os.path.join(cwd, first) if not os.path.isabs(first) else first
        resolved = local_candidate if os.path.isfile(local_candidate) else shutil.which(first)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c", resolved] + cmd[1:]
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT, check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT: превышен лимит выполнения " + str(COMMAND_TIMEOUT) + "с"
    except FileNotFoundError:
        return 127, "", "Команда не найдена: " + cmd[0]
    except subprocess.SubprocessError as e:
        return 1, "", str(e)


def load_automation_artifact(path: str, project_dir: str) -> tuple[dict | None, str | None]:
    """Load the existing tc-to-autotest artifact and confine every declared file."""
    try:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "tc-to-autotest-output.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema_errors = sorted(Draft202012Validator(schema).iter_errors(artifact), key=lambda item: item.json_path)
        if schema_errors:
            return None, f"automation artifact schema invalid: {schema_errors[0].json_path}: {schema_errors[0].message}"
        artifacts = artifact["artifacts"]
        files = artifacts["generated_test_files"]
        methods = artifacts["generated_test_methods"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        return None, f"automation artifact unreadable: {error}"
    root = Path(project_dir).resolve()
    if len({item["id"] for item in files}) != len(files) or len({item["id"] for item in methods}) != len(methods):
        return None, "automation artifact contains duplicate FILE or METHOD identities"
    file_map: dict[str, Path] = {}
    file_specs: dict[str, dict] = {}
    for item in files:
        try:
            raw = Path(item["path"])
            resolved = (root / raw).resolve()
            resolved.relative_to(root)
            if raw.is_absolute() or ".." in raw.parts or not resolved.is_file():
                raise ValueError(item["path"])
            actual_digest = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
            if item["content_digest"].lower() != actual_digest:
                raise ValueError(item["path"])
            if resolved in file_map.values():
                raise ValueError(item["path"])
            if item.get("language") == "java":
                java_root = (root / "src" / "test" / "java").resolve()
                try:
                    resolved.relative_to(java_root)
                except ValueError:
                    raise ValueError("java generated test is outside executable src/test/java")
                source = resolved.read_text(encoding="utf-8")
                package = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
                fqn = (package.group(1) + "." if package else "") + resolved.stem
                duplicates = []
                for sibling in java_root.rglob("*.java"):
                    text = sibling.read_text(encoding="utf-8", errors="replace")
                    sibling_package = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
                    sibling_fqn = (sibling_package.group(1) + "." if sibling_package else "") + sibling.stem
                    if sibling_fqn == fqn:
                        duplicates.append(sibling.resolve())
                if duplicates != [resolved]:
                    raise ValueError("java generated test FQN is not unique in executable source root")
            file_map[item["id"]] = resolved
            file_specs[item["id"]] = item
        except (KeyError, TypeError, ValueError):
            return None, "automation artifact contains an invalid, unavailable, or digest-mismatched generated test file"
    method_map: dict[tuple[str, str], str] = {}
    for method in methods:
        try:
            key = (method["file_id"], method["name"])
            if method["file_id"] not in file_map or key in method_map:
                raise ValueError(method)
            method_map[key] = method["id"]
        except (KeyError, TypeError, ValueError):
            return None, "automation artifact contains ambiguous generated method bindings"
    matrix = artifacts["automation_matrix"]
    method_details = {item["id"]: item for item in methods}
    file_ids = set(file_map)
    referenced_methods: set[str] = set()
    referenced_files: set[str] = set()
    for row in matrix:
        if any(file_id not in file_ids for file_id in row["generated_file_ids"]):
            return None, "automation artifact matrix references an unknown generated file"
        for method_id in row["generated_method_ids"]:
            method = method_details.get(method_id)
            if not method or method["file_id"] not in row["generated_file_ids"] or row["test_case_id"] not in method["test_case_ids"]:
                return None, "automation artifact matrix does not match declared generated files/methods"
            referenced_methods.add(method_id)
            referenced_files.add(method["file_id"])
        expected_files = {method_details[method_id]["file_id"] for method_id in row["generated_method_ids"] if method_id in method_details}
        if set(row["generated_file_ids"]) != expected_files:
            return None, "automation artifact matrix file ids are not exact method ownership"
    if referenced_methods != set(method_details):
        return None, "automation artifact matrix does not cover every generated method"
    if referenced_files != file_ids:
        return None, "automation artifact contains orphan generated files"
    # Method content digests cannot be extracted portably across Python/Java AST
    # variants without a parser. We validate their physical file digest and exact
    # declared method name during runner evidence binding instead of claiming it.
    return {"files": file_map, "file_specs": file_specs, "methods": method_map, "project_root": root, "method_digest_verified": False}, None


def validate_artifact_runner_compatibility(bindings: dict, language: str) -> str | None:
    """Reject mixed or wrong generated source before starting a runner."""
    expected = {"python": ("python", "pytest"), "java": ("java", "junit5")}.get(language)
    if not expected:
        return "automation artifact runner language is unsupported"
    if any((item.get("language"), item.get("framework")) != expected for item in bindings["file_specs"].values()):
        return f"automation artifact files are incompatible with {language} runner"
    return None


def _run_id(project_dir: str, bindings: dict | None) -> str:
    material = str(Path(project_dir).resolve())
    if bindings:
        material += "|" + "|".join(sorted(bindings["methods"].values()))
    return "RUN-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def validate_execution_evidence(verdict: str, run_id: str | None, evidence: list[dict], authoritative: bool) -> list[str]:
    """Public deterministic guard used by pipeline consumers before trace assembly."""
    if authoritative and verdict not in {"PASS", "FAIL"}:
        return ["authoritative evidence requires PASS or FAIL verdict"]
    if verdict == "NOT_RUNNABLE" and (authoritative or run_id is not None or evidence):
        return ["NOT_RUNNABLE must not contain execution evidence"]
    if verdict == "PASS" and any(item.get("status") in {"failed", "error"} for item in evidence):
        return ["PASS cannot contain failed/error execution evidence"]
    if authoritative != bool(run_id and evidence):
        return ["authoritative evidence requires a run_id and non-empty evidence"]
    if not authoritative:
        return [] if run_id is None and not evidence else ["non-authoritative evidence must be empty with null run_id"]
    method_ids = [item.get("method_id") for item in evidence]
    if len(method_ids) != len(set(method_ids)):
        return ["execution evidence contains duplicate method_id"]
    if any(item.get("run_id") != run_id for item in evidence):
        return ["execution evidence run_id does not match top-level run_id"]
    return []


def validate_pytest_target_override(target: str | None, bindings: dict, project_dir: str) -> str | None:
    """Reject targets that do not resolve to exactly one artifact-declared file."""
    if not target:
        return None
    target_file = target.split("::", 1)[0]
    candidate = Path(target_file)
    if not candidate.is_absolute():
        candidate = Path(project_dir) / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return "pytest target cannot be resolved"
    if resolved not in set(bindings["files"].values()):
        return "pytest target is not an exact automation-artifact generated file"
    return None


def bind_pytest_evidence(output: str, bindings: dict, run_id: str) -> tuple[list[dict], list[str]]:
    """Bind pytest node IDs to artifact file identity + method name, deterministically."""
    observed: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    status_map = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "ERROR": "error"}
    line_re = re.compile(r"^(PASSED|FAILED|SKIPPED|ERROR)(?:\s+\[\d+\])?\s+([^\s]+)", re.MULTILINE)
    progress_re = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)\b", re.MULTILINE)
    def file_id_for_node(node_path: str) -> str | None:
        path = Path(node_path)
        if not path.is_absolute():
            path = bindings["project_root"] / path
        try:
            resolved = path.resolve()
        except OSError:
            return None
        matches = [file_id for file_id, declared in bindings["files"].items() if declared == resolved]
        return matches[0] if len(matches) == 1 else None
    for raw_status, nodeid in line_re.findall(output):
        parts = nodeid.split("::")
        if len(parts) < 2:
            continue
        file_id = file_id_for_node(parts[0])
        if not file_id:
            continue
        name = parts[-1].split("[", 1)[0]
        key = (file_id, name)
        if key not in bindings["methods"]:
            continue
        if key in observed and observed[key] != status_map[raw_status]:
            errors.append(f"ambiguous pytest outcome for {name}")
        observed[key] = status_map[raw_status]
    for nodeid, raw_status in progress_re.findall(output):
        parts = nodeid.split("::")
        file_id = file_id_for_node(parts[0])
        if file_id and (file_id, parts[-1].split("[", 1)[0]) in bindings["methods"]:
            observed[(file_id, parts[-1].split("[", 1)[0])] = status_map[raw_status]
    evidence = []
    for key, method_id in sorted(bindings["methods"].items(), key=lambda item: item[1]):
        if key not in observed:
            errors.append(f"missing pytest outcome binding for {method_id}")
            continue
        evidence.append({"run_id": run_id, "method_id": method_id, "status": observed[key]})
    return evidence, errors


def parse_junit_xml_evidence(report_dirs: list[Path], bindings: dict, run_id: str, fresh_reports: set[Path] | None = None) -> tuple[list[dict], list[str]]:
    """Bind Surefire/Gradle JUnit XML testcase records by physical file + method name."""
    observations: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    for report_dir in report_dirs:
        if not report_dir.is_dir():
            continue
        for report in sorted(report_dir.rglob("*.xml")):
            if fresh_reports is not None and report.resolve() not in fresh_reports:
                continue
            try:
                root = ET.parse(report).getroot()
            except ET.ParseError:
                continue
            for case in root.iter("testcase"):
                name = case.attrib.get("name", "").split("[", 1)[0]
                classname = case.attrib.get("classname", "")
                status = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
                matching = []
                for key in bindings["methods"]:
                    path = bindings["files"][key[0]]
                    expected = path.stem
                    try:
                        source = path.read_text(encoding="utf-8")
                        package = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
                        if package:
                            expected = package.group(1) + "." + expected
                    except OSError:
                        continue
                    if key[1] == name and classname == expected:
                        matching.append(key)
                if len(matching) == 1:
                    key = matching[0]
                    if key in observations and observations[key] != status:
                        errors.append(f"ambiguous junit outcome for {name}")
                    observations[key] = status
    evidence = []
    for key, method_id in sorted(bindings["methods"].items(), key=lambda item: item[1]):
        if key not in observations:
            errors.append(f"missing junit outcome binding for {method_id}")
        else:
            evidence.append({"run_id": run_id, "method_id": method_id, "status": observations[key]})
    return evidence, errors


def _report_inventory(report_dir: Path) -> dict[Path, tuple[int, int, str]]:
    """Fingerprint XML reports so a previous test run cannot supply evidence."""
    if not report_dir.is_dir():
        return {}
    inventory = {}
    for path in report_dir.rglob("*.xml"):
        try:
            stat = path.stat()
            inventory[path.resolve()] = (stat.st_mtime_ns, stat.st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError:
            continue
    return inventory


def confined_report_dir(project_dir: str, relative: str) -> Path | None:
    """Resolve the selected runner report tree only when it remains inside project."""
    root = Path(project_dir).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def parse_pytest_output(stdout: str, stderr: str, exit_code: int) -> dict:
    """
    Парсит вывод pytest. Извлекает failed_methods[] и root_cause[].
    pytest exit codes: 0 = PASS, 1 = tests failed, 2+ = collection/usage errors.
    """
    combined = stdout + "\n" + stderr
    lines = combined.splitlines()
    failed_methods = []

    # Формат строки падения pytest (секция "short test summary info"):
    #   FAILED tests/test_part.py::test_tk_01_should_return_200 - AssertionError: ...
    #   ERROR tests/test_x.py::test_y - ImportError: ...
    # pytest сам режет message в summary (до "..."), поэтому ДОПОЛНИТЕЛЬНО достаём
    # полное сообщение из traceback: строки вида "E   assert 3.0 == 999".
    fail_re = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.+))?$")

    # Карта nodeid -> осмысленное сообщение из traceback (E-строки)
    tb_messages = {}  # nodeid -> message
    # Карта nodeid заголовка из "___ test_name ___" -> ближайшие E-строки
    header_re = re.compile(r"^_+\s+(.+?)\s+_+$")
    cur_nodeid = None
    cur_emsg = []
    for line in lines:
        s = line.strip()
        hm = header_re.match(s)
        if hm:
            # сохраним предыдущий
            if cur_nodeid and cur_emsg:
                tb_messages[cur_nodeid] = " ".join(cur_emsg)[:300]
            cur_nodeid = hm.group(1).split("/")[-1]  # имя функции как nodeid-ключ
            cur_emsg = []
            continue
        em = re.match(r"^E\s+(.+)$", s)
        if em and cur_nodeid is not None:
            cur_emsg.append(em.group(1))
    if cur_nodeid and cur_emsg:
        tb_messages[cur_nodeid] = " ".join(cur_emsg)[:300]

    for line in lines:
        m = fail_re.match(line.strip())
        if m:
            kind_raw, nodeid, message = m.group(1), m.group(2), (m.group(3) or "")
            kind = "assertion_failed" if kind_raw == "FAILED" else "error"
            # предпочтём полное сообщение из traceback, если оно есть и не обрезано
            func_name = nodeid.split("::")[-1] if "::" in nodeid else nodeid
            full_msg = tb_messages.get(func_name) or message
            if not full_msg:
                full_msg = message
            failed_methods.append({
                "nodeid": nodeid,
                "kind": kind,
                "message": full_msg[:300],
            })

    # Сводка: парсим по отдельным якорям, чтобы не зависеть от порядка и warnings.
    # Ищем итоговую строку вида "=== N passed, M failed, ... in X.Xs ==="
    stats = {"total": None, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": None}
    summary_line = None
    for line in lines:
        # итоговая строка всегда содержит "in X.Xs" И (passed|failed|error|no tests)
        if " in " in line and ("passed" in line or "failed" in line or "error" in line or "no tests ran" in line) and line.strip().startswith("="):
            summary_line = line
            break
    if summary_line:
        for key, label in [("passed", r"(\d+)\s+passed"),
                           ("failed", r"(\d+)\s+failed"),
                           ("errors", r"(\d+)\s+errors?"),
                           ("skipped", r"(\d+)\s+skipped")]:
            mm = re.search(label, summary_line)
            if mm:
                stats[key] = int(mm.group(1))
        dm = re.search(r"in\s+([\d.]+)s\b", summary_line)
        if dm:
            stats["duration_sec"] = float(dm.group(1))

    # При collection error pytest сообщает число найденных items отдельно:
    # "collected 12 items / 1 error". В summary обычно остаётся только
    # "1 error", поэтому total иначе был бы занижен до 1.
    collected_match = re.search(r"collected\s+(\d+)\s+items?", combined)
    if collected_match:
        stats["total"] = int(collected_match.group(1))
    elif stats["passed"] or stats["failed"] or stats["errors"] or stats["skipped"]:
        stats["total"] = (stats["passed"] + stats["failed"] + stats["errors"] + stats["skipped"])
    elif exit_code == 5:
        # pytest exit 5: коллекция прошла, но не найдено ни одного теста.
        stats["total"] = 0

    # root_cause: группируем failed_methods по типу ошибки (без LLM)
    root_cause = []
    cause_re = re.compile(r"^(AssertionError|ImportError|ModuleNotFoundError|"
                          r"AttributeError|KeyError|TypeError|ValueError|"
                          r"FileNotFoundError|ConnectionError|TimeoutError|"
                          r"Exception|Error)[:\s]")
    # также ловим assert-строки вида "assert 3.0 == 999" как осмысленную причину
    assert_re = re.compile(r"^assert\b")
    seen_causes = set()
    for fm in failed_methods:
        msg = fm["message"]
        cm = cause_re.search(msg)
        if cm:
            cause_key = msg[:120]
        elif assert_re.match(msg):
            cause_key = "AssertionError: " + msg[:100]
        elif msg:
            cause_key = msg[:120]
        else:
            cause_key = "unknown"
        if cause_key not in seen_causes:
            seen_causes.add(cause_key)
            root_cause.append(cause_key)

    # Collection errors (pytest exit 2) часто попадают в summary как
    # "ERROR test_file.py" без текста. Дополняем их реальной E-строкой из
    # traceback, а не оставляем бесполезную причину "unknown".
    if exit_code == 2:
        traceback_errors = [
            line.strip()[2:].strip() for line in lines
            if re.match(r"^E\s+\S", line.strip())
        ]
        for index, failed in enumerate(failed_methods):
            if failed["kind"] == "error" and not failed["message"]:
                failed["kind"] = "collection_error"
                failed["message"] = (
                    traceback_errors[min(index, len(traceback_errors) - 1)][:300]
                    if traceback_errors else "collection_error"
                )

        if not failed_methods:
            coll_re = re.compile(r"^ERROR collecting\s+(\S+)(?:\s+(.+))?$")
            for line in lines:
                m = coll_re.match(line.strip())
                if m:
                    failed_methods.append({
                        "nodeid": m.group(1),
                        "kind": "collection_error",
                        "message": (m.group(2) or "collection_error")[:300],
                    })

        if failed_methods:
            collection_causes = []
            for failed in failed_methods:
                if failed["kind"] == "collection_error":
                    cause = "collection_error: " + failed["message"][:120]
                    if cause not in collection_causes:
                        collection_causes.append(cause)
            if collection_causes:
                root_cause = [cause for cause in root_cause if cause != "unknown"]
                root_cause.extend(cause for cause in collection_causes if cause not in root_cause)

    return {
        "failed_methods": failed_methods,
        "root_cause": root_cause,
        "stats": stats,
    }


def run_python_pytest(project_dir: str, env_bin: str, pytest_target: str | None,
                       extra_args: list | None) -> dict:
    """Запускает pytest, возвращает блок результата."""
    cmd = [env_bin, "-m", "pytest", "-v", "--tb=short", "-rA"]
    if pytest_target:
        cmd.extend(pytest_target if isinstance(pytest_target, list) else [pytest_target])
    if extra_args:
        cmd.extend(extra_args)

    exit_code, stdout, stderr = run_subprocess(cmd, project_dir)
    parsed = parse_pytest_output(stdout, stderr, exit_code)

    if exit_code == 0 and (parsed["stats"]["total"] or 0) > 0:
        verdict = "PASS"
    elif exit_code == 0:
        verdict = "FAIL"
        parsed["root_cause"].append("no_tests_discovered: pytest completed without recognized tests")
    elif exit_code == 5:
        # pytest exit 5 = "no tests collected". Это НЕ то же, что упавшие тесты:
        # тестов физически нет, поэтому подтверждать их прохождение нечем.
        # Честный FAIL с явной причиной — не PASS, но и не «тесты упали».
        verdict = "FAIL"
        if not parsed["root_cause"]:
            parsed["root_cause"] = [
                (
                    "no_tests_discovered: pytest не нашёл ни одного теста "
                    "(проверьте pytest-target / именование test_*.py)"
                )
            ]
    else:
        # exit 2 (collection error) и прочие — это FAIL, тесты не прошли
        verdict = "FAIL"

    return {
        "verdict": verdict,
        "target": {
            "language": "python",
            "framework": "pytest",
            "runner": "pytest",
            "command": " ".join(cmd),
        },
        "exit_code": exit_code,
        "stats": parsed["stats"],
        "failed_methods": parsed["failed_methods"],
        "root_cause": parsed["root_cause"],
        "raw_output_excerpt": _excerpt(stdout + "\n" + stderr),
    }


def run_java(project_dir: str, runner: str, extra_args: list | None) -> dict:
    """Запускает Maven/Gradle test."""
    is_maven = runner.lower().startswith(("mvn", "maven"))
    executable = f"./{runner}" if not is_windows() and runner in {"mvnw", "gradlew"} else runner
    if is_maven:
        cmd = [executable if runner not in {"maven"} else "mvn", "test"] + (extra_args or [])
    elif runner.lower().startswith(("gradle", "gradlew")):
        cmd = [executable if runner != "gradle" else "gradle", "test"] + (extra_args or [])
    else:
        cmd = ["mvn", "test"]

    project_root = Path(project_dir).resolve()
    report_dir = confined_report_dir(project_dir, "target/surefire-reports" if is_maven else "build/test-results")
    if report_dir is None:
        return build_not_runnable({"status": "partial", "interpreter": None, "working_dir": str(project_root), "missing": ["report_dir"]}, "java", "selected report directory escapes project root")
    before_reports = _report_inventory(report_dir)
    exit_code, stdout, stderr = run_subprocess(cmd, project_dir)
    after_reports = _report_inventory(report_dir)
    fresh_reports = {path for path, fingerprint in after_reports.items() if before_reports.get(path) != fingerprint}
    combined = stdout + "\n" + stderr

    # Maven Surefire: "Tests run: 3, Failures: 1, Errors: 0, Skipped: 0"
    stats = {"total": None, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": None}
    matches = list(re.finditer(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)", combined))
    if matches:
        # Maven's final aggregate is authoritative; earlier entries are suites.
        m = matches[-1]
        total = int(m.group(1))
        stats.update({
            "total": total,
            "failed": int(m.group(2)),
            "errors": int(m.group(3)),
            "skipped": int(m.group(4)),
            "passed": total - int(m.group(2)) - int(m.group(3)) - int(m.group(4)),
        })
    elif not is_maven:
        gradle = re.search(r"(\d+) tests? completed(?:, (\d+) failed)?(?:, (\d+) skipped)?", combined)
        if gradle:
            total = int(gradle.group(1))
            failed = int(gradle.group(2) or 0)
            skipped = int(gradle.group(3) or 0)
            stats.update({"total": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped})

    failed_methods = []
    for line in combined.splitlines():
        if "FAILURE!" in line or "ERROR!" in line:
            failed_methods.append({
                "nodeid": line.strip()[:300],
                "kind": "error",
                "message": line.strip()[:300],
            })

    verdict = "PASS" if exit_code == 0 and (stats["total"] or 0) > 0 else "FAIL"
    root_cause = list({fm["message"][:80] for fm in failed_methods})[:10] if failed_methods else []
    if exit_code == 0 and (stats["total"] or 0) == 0:
        root_cause.append("no_tests_discovered: java runner completed without recognized tests")

    return {
        "verdict": verdict,
        "target": {
            "language": "java",
            "framework": "junit5",
            "runner": "maven" if is_maven else "gradle",
            "command": " ".join(cmd),
        },
        "exit_code": exit_code,
        "stats": stats,
        "failed_methods": failed_methods,
        "root_cause": root_cause,
        "raw_output_excerpt": _excerpt(combined),
        "report_dir": report_dir,
        "fresh_reports": fresh_reports,
    }


def _excerpt(text: str) -> str:
    """Сжимает вывод до MAX_OUTPUT_EXCERPT строк, сохраняя хвост (там traceback)."""
    lines = text.splitlines()
    if len(lines) <= MAX_OUTPUT_EXCERPT:
        return text.strip()
    head = "\n".join(lines[:5])
    tail = "\n".join(lines[-MAX_OUTPUT_EXCERPT + 5:])
    return head + f"\n...\n[усечено {len(lines) - MAX_OUTPUT_EXCERPT} строк]\n...\n" + tail


# ---------------------------------------------------------------------------
# Сборка финального отчёта по контракту
# ---------------------------------------------------------------------------

def build_report(verdict: str, target: dict, environment: dict,
                 stats: dict | None, failed_methods: list, root_cause: list,
                 raw_output_excerpt: str | None, exit_code: int | None,
                 run_id: str | None = None, execution_evidence: list | None = None,
                 evidence_authoritative: bool = False) -> dict:
    """Собирает отчёт строго по schemas/run-tests-output.schema.json."""
    # убираем внутреннее поле
    env_out = {k: v for k, v in environment.items() if not k.startswith("_")}
    return {
        "verdict": verdict,
        "target": target,
        "environment": env_out,
        "stats": stats,
        "failed_methods": failed_methods or None,
        "root_cause": root_cause or None,
        "raw_output_excerpt": raw_output_excerpt,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
        "run_id": run_id,
        "execution_evidence": execution_evidence or [],
        "evidence_authoritative": evidence_authoritative,
    }


def build_not_runnable(environment: dict, language: str, reason: str) -> dict:
    """Собирает честный NOT_RUNNABLE-отчёт."""
    return build_report(
        verdict="NOT_RUNNABLE",
        target={
            "language": language,
            "framework": environment.get("_framework") or "unknown",
            "runner": "not_applicable",
            "command": None,
        },
        environment=environment,
        stats=None,
        failed_methods=None,
        root_cause=[reason] if reason else None,
        raw_output_excerpt=None,
        exit_code=None,
    )


def build_internal_error_report(error: Exception) -> dict:
    """Return the same schema-compatible NOT_RUNNABLE shape used by the CLI guard."""
    return build_not_runnable(
        {
            "status": "missing",
            "interpreter": None,
            "interpreter_path": None,
            "working_dir": os.getcwd(),
            "missing": ["runner_internal_error"],
            "_framework": "unknown",
        },
        "unknown",
        "runner_internal_error: " + str(error)[:200],
    )


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(
        description="Детерминированный оракул исполнения автотестов (Опора 1). "
                    "Выводит JSON-вердикт PASS|FAIL|NOT_RUNNABLE по схеме schemas/run-tests-output.schema.json."
    )
    parser.add_argument("--project", help="Корень репозитория; рабочая директория выбирается из module root .skillsrc")
    parser.add_argument("--skillsrc", help="Путь к .skillsrc — стек определяется из манифеста")
    parser.add_argument("--module", help="ID модуля из .skillsrc версии 3")
    parser.add_argument("--language", choices=["python", "java", "go", "typescript", "kotlin"],
                        help="Язык тестов (если не указан — берётся из .skillsrc)")
    parser.add_argument("--pytest-target", help="Python: конкретный файл/директория pytest "
                                                 "(по умолчанию — автообнаружение pytest'ом)")
    parser.add_argument("--pytest-args", help="Дополнительные аргументы pytest (через запятую)")
    parser.add_argument("--python-executable", help="Python interpreter override for both pytest probing and execution")
    parser.add_argument("--automation-artifact", help="Validated tc-to-autotest JSON artifact used to select and bind generated methods")
    args = parser.parse_args()

    # --- Определяем язык/проект ---
    project_root = Path(args.project or os.getcwd()).resolve()
    skillsrc_path = Path(args.skillsrc or project_root / ".skillsrc")
    project_dir = str(project_root)
    language = args.language
    bindings = None
    artifact_error = None
    if skillsrc_path.is_file():
        try:
            execution_root, language, _module = resolve_execution_context(
                project_root, skillsrc_path, args.module, language
            )
        except SkillsrcError as error:
            missing = (
                "module_selection"
                if error.code in {"module_required", "module_unknown", "module_ambiguous"}
                else "language_conflict"
                if error.code == "language_conflict"
                else "skillsrc_validation"
            )
            report = build_not_runnable(
                {
                    "status": "missing",
                    "interpreter": None,
                    "interpreter_path": None,
                    "working_dir": str(project_root),
                    "missing": [missing],
                },
                language or "unknown",
                str(error),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        project_dir = str(execution_root)
    elif not language:
        env = {
            "status": "missing",
            "interpreter": None,
            "working_dir": str(project_root),
            "missing": ["language_detection"],
        }
        report = build_not_runnable(
            env, "unknown",
            "Не удалось определить язык проекта: .skillsrc не найден. "
            "Укажите --language явно или положите .skillsrc.",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if args.automation_artifact:
        bindings, artifact_error = load_automation_artifact(args.automation_artifact, project_dir)
        if artifact_error:
            report = build_not_runnable(
                {"status": "partial", "interpreter": None, "interpreter_path": None,
                 "working_dir": os.path.abspath(project_dir), "missing": ["automation_artifact"], "_framework": "unknown"},
                language or "unknown", artifact_error,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    if bindings:
        compatibility_error = validate_artifact_runner_compatibility(bindings, language)
        if compatibility_error:
            report = build_not_runnable(
                {"status": "partial", "interpreter": None, "interpreter_path": None,
                 "working_dir": os.path.abspath(project_dir), "missing": ["automation_artifact"], "_framework": "unknown"},
                language, compatibility_error,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    # --- Проверяем окружение под язык ---
    if language == "python":
        if bindings:
            if args.pytest_args:
                report = build_not_runnable(
                    {"status": "partial", "interpreter": None, "interpreter_path": None,
                     "working_dir": os.path.abspath(project_dir), "missing": ["pytest_args"], "_framework": "pytest"},
                    "python", "pytest arguments are rejected with authoritative automation evidence",
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2
            target_error = validate_pytest_target_override(args.pytest_target, bindings, project_dir)
            if target_error:
                report = build_not_runnable(
                    {"status": "partial", "interpreter": None, "interpreter_path": None,
                     "working_dir": os.path.abspath(project_dir), "missing": ["pytest_target"], "_framework": "pytest"},
                    "python", target_error,
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2
        env = check_python_env(project_dir, args.python_executable)
        env["_framework"] = "pytest"
        if env["status"] == "missing" or not env.get("_interpreter_bin"):
            report = build_not_runnable(
                env, "python",
                "Окружение python недоступно: " + ", ".join(env["missing"] or ["unknown"]),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        if env["status"] == "partial":
            # pytest есть, но, например, проекта нет — всё равно NOT_RUNNABLE
            report = build_not_runnable(
                env, "python",
                "Окружение python частично доступно, не хватает: " + ", ".join(env["missing"]),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        # --- Запуск pytest ---
        extra = args.pytest_args.split(",") if args.pytest_args else None
        selected_targets = args.pytest_target
        if bindings and not selected_targets:
            selected_targets = [str(path) for path in bindings["files"].values()]
        result = run_python_pytest(project_dir, env["_interpreter_bin"], selected_targets, extra)
        run_id = _run_id(project_dir, bindings) if bindings else None
        evidence, binding_errors = bind_pytest_evidence(
            result["raw_output_excerpt"], bindings, run_id
        ) if bindings and run_id else ([], [])
        if binding_errors:
            result["verdict"] = "FAIL"
            result["root_cause"].extend(binding_errors)
            result["exit_code"] = 1
        report = build_report(
            verdict=result["verdict"],
            target=result["target"],
            environment=env,
            stats=result["stats"],
            failed_methods=result["failed_methods"],
            root_cause=result["root_cause"],
            raw_output_excerpt=result["raw_output_excerpt"],
            exit_code=result["exit_code"],
            run_id=run_id,
            execution_evidence=evidence,
            evidence_authoritative=bool(bindings and not binding_errors),
        )
        semantic_errors = validate_execution_evidence(report["verdict"], report["run_id"], report["execution_evidence"], report["evidence_authoritative"])
        if semantic_errors:
            report["verdict"] = "FAIL"
            report["root_cause"] = (report["root_cause"] or []) + semantic_errors
            report["exit_code"] = 1
            report["run_id"] = None
            report["execution_evidence"] = []
            report["evidence_authoritative"] = False

    elif language == "java":
        env = check_java_env(project_dir)
        env["_framework"] = "junit5"
        if env["status"] != "ready":
            report = build_not_runnable(
                env, "java",
                "Окружение java недоступно: " + ", ".join(env["missing"] or ["unknown"]),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        result = run_java(project_dir, env.get("_runner") or "mvn", None)
        run_id = _run_id(project_dir, bindings) if bindings else None
        evidence, binding_errors = parse_junit_xml_evidence(
            [result["report_dir"]], bindings, run_id, result["fresh_reports"],
        ) if bindings and run_id else ([], [])
        if binding_errors:
            result["verdict"] = "FAIL"
            result["root_cause"].extend(binding_errors)
            result["exit_code"] = 1
        report = build_report(
            verdict=result["verdict"],
            target=result["target"],
            environment=env,
            stats=result["stats"],
            failed_methods=result["failed_methods"],
            root_cause=result["root_cause"],
            raw_output_excerpt=result["raw_output_excerpt"],
            exit_code=result["exit_code"],
            run_id=run_id,
            execution_evidence=evidence,
            evidence_authoritative=bool(bindings and not binding_errors),
        )
        semantic_errors = validate_execution_evidence(report["verdict"], report["run_id"], report["execution_evidence"], report["evidence_authoritative"])
        if semantic_errors:
            report["verdict"] = "FAIL"
            report["root_cause"] = (report["root_cause"] or []) + semantic_errors
            report["exit_code"] = 1
            report["run_id"] = None
            report["execution_evidence"] = []
            report["evidence_authoritative"] = False

    else:
        # go / typescript / kotlin — заглушка: честный NOT_RUNNABLE, пока не реализован раннер
        env = {
            "status": "missing",
            "interpreter": None,
            "working_dir": os.path.abspath(project_dir),
            "missing": ["runner_not_implemented:" + language],
            "_framework": "unknown",
        }
        report = build_not_runnable(
            env, language,
            f"Раннер для {language} ещё не реализован в tools/run_tests.py. "
            "Это честный NOT_RUNNABLE — не ПРИНЯТО.",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))

    # exit codes для CI: 0=PASS, 1=FAIL, 2=NOT_RUNNABLE/internal error
    if report["verdict"] == "FAIL":
        return 1
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — раннер не должен падать молча
        # Ошибка самого раннера — отдаём структурированный отчёт, а не трейс
        print(json.dumps(build_internal_error_report(e), ensure_ascii=False, indent=2))
        sys.exit(2)
