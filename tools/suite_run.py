"""Run the whole suite (wave 3, F/R): every method of the manifest, quarantined ones explicitly.

The command is the module's closed adapter command (``execution_adapters.command_for``) with the
suite's selectors; a quarantined JUnit 5 method runs by deactivating ``@Disabled`` and a pytest one
with ``--runxfail`` (contract amendment A4), so a fixed defect shows up.  A method that fails is run
again up to ``repeats`` times: a stable failure stays a failure, a changing result is ``FLAKY``
(``suite_failures.classify``).  ``RUNNER`` is the process seam the tests replace with a fake runner.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
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


def runtime_executable(module: Mapping[str, Any]) -> str:
    """The launcher of the module's closed adapter, resolved as on the main path (security review note 4).

    A module-local wrapper or interpreter goes through ``module_runtime_path``, system Maven through
    ``system_maven_path``; any other executable of the ``.skillsrc`` is ``AdapterRequestError`` (NOT_RUNNABLE).
    """
    from tools.project_inventory import InventoryError, system_maven_path

    adapter_id = module["test"]["adapter_id"]
    if adapter_id in {adapters.PYTEST, adapters.MAVEN, adapters.GRADLE}:
        base, runtime = adapters.resolve_module_runtime(module)
        return str(base / runtime)
    if adapter_id == adapters.SYSTEM_MAVEN:
        try:
            return str(system_maven_path(module["test"].get("executable")))
        except (InventoryError, OSError) as error:
            raise adapters.AdapterRequestError(f"system Maven is unavailable or unsafe: {error}") from error
    raise adapters.AdapterRequestError(f"adapter {adapter_id!r} is not in the closed pilot set")


class HumanSuitePending(BaseException):
    """S9b execution.mode human: a person must run the suite command first (passes error handling, like an interruption)."""

    def __init__(self, command: Mapping[str, Any]) -> None:
        self.command = dict(command)
        super().__init__(f"human suite step {command.get('step')}")


class HumanStepIncomplete(ValueError):
    def __init__(self, message: str) -> None:
        self.code = "HUMAN_STEP_INCOMPLETE"
        super().__init__(message)


def _human_state(workdir: Path) -> dict[str, Any]:
    path = Path(workdir) / "human.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"issued": [], "done": {}}


def _save_human_state(workdir: Path, state: Mapping[str, Any]) -> None:
    Path(workdir).mkdir(parents=True, exist_ok=True)
    (Path(workdir) / "human.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def _report_files(report: Path) -> list[Path]:
    """The non-empty JUnit reports of an adapter's report location (a directory or one file)."""
    candidates = [report] if report.is_file() else sorted(report.rglob("*.xml")) if report.is_dir() else []
    return [path for path in candidates if path.is_file() and path.stat().st_size > 0]


def _human_digest(argv: Sequence[str]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(list(argv), ensure_ascii=False).encode("utf-8")).hexdigest()


def _human_run(workdir: Path, argv: Sequence[str], cwd: str, report: Path, step: str) -> Any:
    """Start nothing: a command a person ran (its reports present) returns their exit code, any other stops the run.

    The step is part of the key: the repair run repeats the very command of the suite run, and is a new step.
    """
    digest = _human_digest([step, *argv])
    state = _human_state(workdir)
    if digest in state["done"]:
        done = state["done"][digest]
        if _report_files(report) or done["returncode"] != 0:
            # The person's build log (if given) is the output: compile errors are found in it as in a package run.
            return SimpleNamespace(returncode=int(done["returncode"]), stdout=str(done.get("output") or "human-executed"), stderr="")
        # Done once, but the reports are gone: the step is issued again (never a dead end).
        state["done"].pop(digest)
        state["issued"] = [row for row in state["issued"] if row["digest"] != digest]
    if digest not in {row["digest"] for row in state["issued"]}:
        # Only the adapter's report location is removed (not target/): its new content proves the person's run.
        if report.is_dir():
            shutil.rmtree(report, ignore_errors=True)
        elif report.is_file():
            report.unlink()
        state["issued"].append({"digest": digest, "step": step, "expected_output": str(report)})
        _save_human_state(workdir, state)
    raise HumanSuitePending({"step": step, "argv": [str(item) for item in argv], "cwd": str(cwd), "expected_output": str(report)})


def human_continue(workdir: Path, reports: Sequence[str], exit_code: int | None, build_log: str | None = None) -> None:
    """Mark the issued suite command done (S9b): its reports exist inside its report location, or it failed before any report.

    A build that failed before the tests (a compile error) writes no report: the person passes a nonzero ``--exit-code``
    and, to let the package find the failing file as in its own run, the build output with ``--build-log``.
    """
    state = _human_state(workdir)
    pending = [row for row in state["issued"] if row["digest"] not in state["done"]]
    if not pending:
        raise HumanStepIncomplete("no suite command is waiting for its result")
    row = pending[-1]
    expected = Path(row["expected_output"]).resolve()
    for raw in reports:
        path = Path(raw).resolve()
        if not (path == expected or path.is_relative_to(expected)) or not path.is_file() or path.stat().st_size == 0:
            raise HumanStepIncomplete(f"a report must be a non-empty file of {expected}: {raw}")
    output = None
    if build_log is not None:
        log = Path(build_log)
        if not log.is_file():
            raise HumanStepIncomplete(f"the build log is not a file: {build_log}")
        output = log.read_text(encoding="utf-8", errors="replace")[-20000:]
    if not reports or not _report_files(expected):
        if exit_code is None or int(exit_code) == 0:
            raise HumanStepIncomplete(f"the command wrote no report into {expected}: pass --junit <report>, or a nonzero --exit-code "
                                      "(and --build-log <file>) when the build failed before the tests")
    elif exit_code is None:
        rows = _report_rows(expected)
        exit_code = 1 if any(item["status"] in {"failed", "broken"} for item in rows) else 0
    state["done"][row["digest"]] = {"returncode": int(exit_code), **({"output": output} if output is not None else {})}
    _save_human_state(workdir, state)


def run_once(project: Path, module: Mapping[str, Any], selectors: Sequence[str], *, explicit: bool, timeout: int,
             human: Mapping[str, Any] | None = None) -> dict[str, Any]:
    module_root = (Path(project) / str(module.get("root") or ".")).resolve()
    request_module = {**module, "module_root": str(module_root)}
    adapter_id = request_module["test"]["adapter_id"]
    layout = adapters.launch_layout(request_module)
    executable = runtime_executable(request_module)
    reports, argv = adapters.command_for(adapter_id, executable, str(request_module["test"]["build_profile"]), list(selectors), module_path=layout.module_path,
                                         offline=layout.offline)
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
    if human is not None:
        completed = _human_run(Path(human["workdir"]), argv, str(layout.build_root), report_dir, str(human["step"]))
    else:
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
              only: set[str] | None = None, human: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
        try:
            outcome = run_once(project, module, pending, explicit=explicit, timeout=timeout, human=human)
        except adapters.AdapterRequestError as error:
            return {"methods": [methods[locator] for locator in sorted(methods)], "commands": commands, "execution": execution,
                    "build_broken": [], "not_runnable": str(error)}
        commands.append({"argv": outcome["argv"], "returncode": outcome["returncode"], "selectors": len(pending), "reported": len(outcome["rows"]),
                         "output_tail": outcome["output_tail"][-1500:]})
        execution = execution or outcome["execution"]
        seen = set()
        # The invocations of a parameterized method (``m(String)[1]``, ``test_x[a]``) are one outcome per run:
        # the worst of them — never a sequence of repeats that would read as FLAKY (independent review 2.3).
        severity = {"skipped": 0, "passed": 1, "failed": 2, "broken": 3}
        this_run: dict[str, str] = {}
        asked = set(pending)
        for row in outcome["rows"]:
            selector = _row_selector(row, adapter_id)
            locator = by_selector.get(selector)
            if locator is None or selector not in asked:
                continue
            seen.add(selector)
            if locator not in this_run or severity[row["status"]] > severity[this_run[locator]]:
                this_run[locator] = row["status"]
            if row["status"] in {"failed", "broken"} and methods[locator]["failure"] is None:
                methods[locator]["failure"] = row.get("message") or None
            if row["status"] == "broken" and methods[locator]["error_origin"] != "PRODUCT":
                if product is None:
                    module_root_path = (Path(project) / module_root).resolve()
                    product = product_classes(module_root_path, list(((module.get("paths") or {}).get("source")) or ["src/main/java"]))
                methods[locator]["error_origin"] = error_origin(row.get("trace"), row.get("message"), product)
        for locator, status in this_run.items():
            methods[locator]["runs"].append(status)
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
    The launcher of the ``.skillsrc`` is not a closed runtime — ``NOT_RUNNABLE``, nothing started.
    """
    if result.get("not_runnable"):
        return "NOT_RUNNABLE", f"the suite was not started: {result['not_runnable']}"
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
