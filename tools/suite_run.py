"""Run the whole suite (wave 3, F/R): every method of the manifest, quarantined ones explicitly.

The command is the module's closed adapter command (``execution_adapters.command_for``) with the
suite's selectors; a quarantined JUnit 5 method runs by deactivating ``@Disabled`` and a pytest one
with ``--runxfail`` (contract amendment A4), so a fixed defect shows up.  A method that fails is run
again up to ``repeats`` times: a stable failure stays a failure, a changing result is ``FLAKY``
(``suite_failures.classify``).  ``RUNNER`` is the process seam the tests replace with a fake runner.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import execution_adapters as adapters
from tools.quarantine import EXPLICIT_RUN
from tools.suite_impact import junit_outcomes

RUNNER: Callable[..., Any] = subprocess.run
_COMPILE_ERROR = re.compile(r"\[ERROR\]\s+(?:/)?([A-Za-z]:)?([^\s\[]+\.java):\[(\d+)")
# javac through Gradle (and plain javac): ``path/File.java:42: error: …``
_JAVAC_ERROR = re.compile(r"^(?:/)?([A-Za-z]:)?(\S[^\n:]*?\.java):(\d+): error:", re.M)


def compile_lines_of(output: str) -> list[tuple[str, int]]:
    """``(path, line)`` of every compile error a Maven or Gradle build names, ``/`` separated, without the drive."""
    found = {(match.group(2).replace("\\", "/"), int(match.group(3))) for pattern in (_COMPILE_ERROR, _JAVAC_ERROR) for match in pattern.finditer(output)}
    return sorted(found)


_FRAME = re.compile(r"^\s*at ([\w$.]+)\.[\w$<>]+\(", re.M)


def product_classes(module_root: Path, sources: Sequence[str]) -> set[str]:
    """Fully qualified names of the product's Java classes (``a/b/C.java`` under a source root is ``a.b.C``)."""
    names = set()
    for source in sources:
        root = Path(module_root) / source
        if root.is_dir():
            names |= {".".join(path.relative_to(root).with_suffix("").parts) for path in root.rglob("*.java")}
    return names


def error_origin(trace: str | None, message: str | None, product: set[str]) -> str:
    """``PRODUCT`` when the error came out of the product's code, else ``TEST`` (review 2.1 item 11).

    A frame of a product class anywhere in the trace (the ``Caused by`` chain included) or MockMvc's
    ``Request processing failed`` (the handler threw) is the product's; anything else broke in the
    test's own code and is a repair.
    """
    text = f"{message or ''}\n{trace or ''}"
    if "Request processing failed" in text:
        return "PRODUCT"
    for match in _FRAME.finditer(text):
        if match.group(1).split("$", 1)[0] in product:
            return "PRODUCT"
    return "TEST"


def selector_of(method: Mapping[str, Any], module_root: str) -> str:
    """Java ``class#method``; pytest ``module-relative path::function`` or ``::Class::method``."""
    locator = method["locator"]
    if "#" in locator:
        return locator
    path = method["file"]
    prefix = module_root.strip("./")
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix) + 1:]
    return f"{path}::{locator.replace('.', '::')}"


def _report_rows(report_dir: Path) -> list[dict[str, str]]:
    files = sorted(report_dir.glob("TEST-*.xml")) if report_dir.is_dir() else [report_dir] if report_dir.is_file() else []
    return junit_outcomes(files) if files else []


def _row_selector(row: Mapping[str, str], adapter_id: str) -> str:
    if adapter_id == adapters.PYTEST:
        module, _, owner = row["classname"].rpartition(".")
        return f"{module.replace('.', '/')}.py::{owner}::{row['name']}" if owner and owner[:1].isupper() else f"{row['classname'].replace('.', '/')}.py::{row['name']}"
    return f"{row['classname']}#{row['name']}"


def run_once(project: Path, module: Mapping[str, Any], selectors: Sequence[str], *, explicit: bool, timeout: int) -> dict[str, Any]:
    module_root = (Path(project) / str(module.get("root") or ".")).resolve()
    request_module = {**module, "module_root": str(module_root)}
    adapter_id = request_module["test"]["adapter_id"]
    layout = adapters.launch_layout(request_module)
    base, runtime = adapters.resolve_module_runtime(request_module) if adapter_id in {adapters.PYTEST, adapters.MAVEN, adapters.GRADLE} else (layout.build_root, str(request_module["test"]["executable"]))
    executable = str(base / runtime) if adapter_id in {adapters.PYTEST, adapters.MAVEN, adapters.GRADLE} else runtime
    reports, argv = adapters.command_for(adapter_id, executable, str(request_module["test"]["build_profile"]), list(selectors), module_path=layout.module_path)
    argv = list(argv)
    explicit_applied = False
    if explicit:
        if adapter_id in {adapters.MAVEN, adapters.SYSTEM_MAVEN}:
            argv.insert(argv.index("test"), EXPLICIT_RUN["junit5"])
            explicit_applied = True
        elif adapter_id == adapters.PYTEST:
            argv.append(EXPLICIT_RUN["pytest"])
            explicit_applied = True
    report_dir = module_root / reports[0]
    if report_dir.is_dir():
        shutil.rmtree(report_dir, ignore_errors=True)
    elif report_dir.is_file():
        report_dir.unlink()
    completed = RUNNER(argv, cwd=str(layout.build_root), capture_output=True, text=True, timeout=timeout, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    rows = _report_rows(report_dir)
    compile_lines = compile_lines_of(output)
    compile_files = sorted({path for path, _line in compile_lines})
    compile_error = completed.returncode != 0 and not rows and ("COMPILATION ERROR" in output or bool(compile_files) or "error:" in output.lower())
    execution = {"adapter_id": adapter_id, "argv": argv, "cwd": str(layout.build_root), "executable_path": executable,
                 "build_profile": str(request_module["test"]["build_profile"])}
    return {"argv": argv, "returncode": completed.returncode, "rows": rows, "compile_error": compile_error, "compile_files": compile_files, "compile_lines": compile_lines,
            "execution": execution,
            "explicit_applied": explicit_applied, "output_tail": output[-4000:]}


def _blame(project: Path, manifest: Mapping[str, Any], methods: dict[str, dict[str, Any]], outcome: Mapping[str, Any]) -> list[str]:
    """A compile error belongs to the method whose lines it names; the other methods of the build did not run.

    Returns the errors that belong to no method — imports, SUPPORT, another file, the product's code,
    or an error without a line.  A method repair cannot fix those (and quarantine cannot make the
    module compile), so the run stops for a person (review 2.1 item 4).
    """
    from tools.suite_manifest import parse_locator, slices_of

    files = {row["path"]: row for row in manifest.get("files") or []}
    outside: list[str] = []
    lines_of: dict[str, list[int]] = {}
    for path, line in outcome.get("compile_lines") or []:
        own = next((name for name in files if path.endswith(name) or name.endswith(path)), None)
        if own is None:
            outside.append(f"{path}:{line}")
        else:
            lines_of.setdefault(own, []).append(line)
    if not outcome.get("compile_lines"):
        outside.append("the build output names no file and line")
    for row in methods.values():
        row["runs"].append("skipped")
    for path, numbers in lines_of.items():
        rows = [row for row in methods.values() if row["file"] == path]
        file_row = files[path]
        try:
            content = (Path(project) / path).read_text(encoding="utf-8")
            slices = slices_of(path, file_row["file_id"], content, [{"symbol_id": row["locator"], "locator": parse_locator(row["locator"], file_row["language"])} for row in rows])
        except (OSError, ValueError):
            slices = None
        for number in numbers:
            hit = [row for row in rows if slices is not None and slices.symbols[row["locator"]].start <= number <= slices.symbols[row["locator"]].end]
            if not hit:
                outside.append(f"{path}:{number}")
            for row in hit:
                row["compile_error"] = True
                row["runs"] = []
                row["failure"] = outcome["output_tail"][-500:]
    return sorted(set(outside))


def run_suite(project: Path, manifest: Mapping[str, Any], module: Mapping[str, Any], *, repeats: int = 2, timeout: int = 1800,
              only: set[str] | None = None) -> dict[str, Any]:
    """Statuses per method locator over the first run and the repeats of the failed ones."""
    module_root = str(module.get("root") or ".")
    adapter_id = module["test"]["adapter_id"]
    methods: dict[str, dict[str, Any]] = {}
    for case in manifest["cases"]:
        if case["status"] == "RETIRED":
            continue
        for method in case["methods"]:
            if only is not None and method["locator"] not in only:
                continue
            row = methods.setdefault(method["locator"], {"locator": method["locator"], "file": method["file"], "case_ids": [], "runs": [], "failure": None,
                                                         "compile_error": False, "quarantined": case["status"] == "QUARANTINED", "error_origin": None})
            row["case_ids"].append(case["case_id"])
    if not methods:
        return {"methods": [], "commands": [], "execution": None, "build_broken": []}
    by_selector = {selector_of(row, module_root): locator for locator, row in methods.items()}
    explicit = any(row["quarantined"] for row in methods.values())
    commands = []
    execution = None
    product: set[str] | None = None
    build_broken: list[str] = []
    pending = sorted(by_selector)
    for attempt in range(1 + max(0, int(repeats))):
        if not pending:
            break
        outcome = run_once(project, module, pending, explicit=explicit, timeout=timeout)
        commands.append({"argv": outcome["argv"], "returncode": outcome["returncode"], "selectors": len(pending), "reported": len(outcome["rows"]),
                         "output_tail": outcome["output_tail"][-1500:]})
        execution = execution or outcome["execution"]
        seen = set()
        for row in outcome["rows"]:
            selector = _row_selector(row, adapter_id)
            locator = by_selector.get(selector)
            if locator is None:
                continue
            seen.add(selector)
            methods[locator]["runs"].append(row["status"])
            if row["status"] in {"failed", "broken"} and methods[locator]["failure"] is None:
                methods[locator]["failure"] = row.get("message") or None
            if row["status"] == "broken" and methods[locator]["error_origin"] != "PRODUCT":
                if product is None:
                    module_root_path = (Path(project) / module_root).resolve()
                    product = product_classes(module_root_path, list(((module.get("paths") or {}).get("source")) or ["src/main/java"]))
                methods[locator]["error_origin"] = error_origin(row.get("trace"), row.get("message"), product)
        if outcome["compile_error"]:
            build_broken = _blame(project, manifest, methods, outcome)
            break
        for selector in pending:
            if selector not in seen:
                methods[by_selector[selector]]["runs"].append("skipped")
        if attempt == 0 and explicit and not outcome["explicit_applied"]:
            for row in methods.values():
                if row["quarantined"]:
                    row["runs"] = ["skipped"]
        pending = [selector for selector in pending if methods[by_selector[selector]]["runs"][-1] in {"failed", "broken"}]
    return {"methods": [methods[locator] for locator in sorted(methods)], "commands": commands, "execution": execution, "build_broken": build_broken}


def not_run_reason(result: Mapping[str, Any]) -> tuple[str, str] | None:
    """Why a suite run is no evidence at all, or None.

    Nothing executed (no JDK, unresolved dependencies, a wrong module: no report and no compile
    error) — ``SUITE_NOT_RUN``; the build failed although every reported method passed —
    ``SUITE_RUN_NONZERO_EXIT``; a compile error outside every method — ``SUITE_BUILD_BROKEN``.  Either
    way the run proves nothing about the methods and must not read as green or end in quarantine.
    """
    methods = result.get("methods") or []
    commands = result.get("commands") or []
    if not methods or not commands:
        return None
    codes = ", ".join(str(row["returncode"]) for row in commands)
    tail = " ".join(str(commands[0].get("output_tail") or "").split())[-600:]
    if result.get("build_broken"):
        return "SUITE_BUILD_BROKEN", ("the build does not compile outside the suite's methods (" + ", ".join(result["build_broken"][:8])
                                      + "): no method is repaired or quarantined; a person fixes the build: " + (tail or "no output"))
    if any(row["compile_error"] for row in methods):
        return None
    if not any(status != "skipped" for row in methods for status in row["runs"]):
        return "SUITE_NOT_RUN", f"the suite run executed no test (exit {codes}): {tail or 'no output'}"
    failed = any(status in {"failed", "broken"} for row in methods for status in row["runs"])
    if not failed and any(row["returncode"] != 0 for row in commands):
        return "SUITE_RUN_NONZERO_EXIT", f"the build exited with {codes} although every reported test passed: {tail or 'no output'}"
    return None
