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
_COMPILE_ERROR = re.compile(r"\[ERROR\]\s+(?:/)?([A-Za-z]:)?([^\s\[]+\.java):\[\d+")


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
    compile_files = sorted({match.group(2).replace("\\", "/") for match in _COMPILE_ERROR.finditer(output)})
    compile_error = completed.returncode != 0 and not rows and ("COMPILATION ERROR" in output or bool(compile_files) or "error:" in output.lower())
    return {"argv": argv, "returncode": completed.returncode, "rows": rows, "compile_error": compile_error, "compile_files": compile_files,
            "explicit_applied": explicit_applied, "output_tail": output[-4000:]}


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
                                                         "compile_error": False, "quarantined": case["status"] == "QUARANTINED"})
            row["case_ids"].append(case["case_id"])
    if not methods:
        return {"methods": [], "commands": []}
    by_selector = {selector_of(row, module_root): locator for locator, row in methods.items()}
    explicit = any(row["quarantined"] for row in methods.values())
    commands = []
    pending = sorted(by_selector)
    for attempt in range(1 + max(0, int(repeats))):
        if not pending:
            break
        outcome = run_once(project, module, pending, explicit=explicit, timeout=timeout)
        commands.append({"argv": outcome["argv"], "returncode": outcome["returncode"], "selectors": len(pending)})
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
        if outcome["compile_error"]:
            broken_files = set(outcome["compile_files"])
            for selector in pending:
                row = methods[by_selector[selector]]
                if not broken_files or any(row["file"].endswith(path.split("/", 1)[-1]) or path.endswith(row["file"]) for path in broken_files):
                    row["compile_error"] = True
                    row["failure"] = outcome["output_tail"][-500:]
            break
        for selector in pending:
            if selector not in seen:
                methods[by_selector[selector]]["runs"].append("skipped")
        if attempt == 0 and explicit and not outcome["explicit_applied"]:
            for row in methods.values():
                if row["quarantined"]:
                    row["runs"] = ["skipped"]
        pending = [selector for selector in pending if methods[by_selector[selector]]["runs"][-1] in {"failed", "broken"}]
    return {"methods": [methods[locator] for locator in sorted(methods)], "commands": commands}
