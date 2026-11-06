"""The commands of the generated tests: ``materialize`` puts the accepted tests into the project and prints the command, a human runs
it, ``ingest`` takes the reports; ``exec`` only answers that it was replaced.  The core reaches this module through ``tools.execution_gate``.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.run_pipeline import HostStop, command_token
from tools.execution_core import (
    automation_split_fields as _automation_split,
    baseline_check as _baseline_check,
    drift_report as _drift_report,
    print_payload as _print,
    quarantine_summary as _quarantine_summary,
    read_run_coordinate as _read_run_coordinate,
    report_mismatch as _report_mismatch,
    style_notes as _style_notes,
    canonical_digest as _canonical_digest,
    publish_fixed_receipt_resuming as _publish_fixed_receipt_resuming,
    report_digest_evidence as _report_digest_evidence,
    report_snapshots as _report_snapshots,
    runner_report as _core_report,
    process_evidence_row as _core_process_row,
)
from tools.run_tests import ProcessOutcome


def _resolve_host_context(
    project: Path,
    module_id: str | None,
    language_override: str | None,
    skillsrc_document: Mapping[str, Any] | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    from tools.run_tests import resolve_execution_context, resolve_execution_context_document
    from tools.skillsrc_manifest import SkillsrcError

    if skillsrc_document is None:
        execution_root, language, module = resolve_execution_context(project, project / ".skillsrc", module_id, language_override)
    else:
        execution_root, language, module = resolve_execution_context_document(
            project, skillsrc_document, module_id, language_override
        )
    if language not in {"python", "java"}:
        raise SkillsrcError(
            "language_unsupported",
            "selected module language is unsupported by the V5 runner",
        )
    expected_framework = "pytest" if language == "python" else "junit5"
    if (module.get("test") or {}).get("framework") != expected_framework:
        raise SkillsrcError("test_framework_unsupported", f"selected {language} module requires {expected_framework}")
    return execution_root, language, module


def _validated_execution_coordinates(project: Path, run_id: str) -> dict[str, Any]:
    """Bind CLI execution to one durable local-pilot run and its latest attempt."""
    root, run, state = _read_run_coordinate(project, run_id)
    manifest = run["manifest"]
    authorization = run.get("authorization")
    if (
        not isinstance(authorization, Mapping)
        or authorization.get("execution_requested") is not True
        or authorization.get("policy_profile") != "local-pilot-v1"
    ):
        raise HostStop("EXECUTION_COORDINATES", "run lacks local execution authorization; a run without execution "
                       "(cases-only or lowered to it) closes with run_pipeline finish")
    attempts = state.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise HostStop("EXECUTION_COORDINATES", "run has no attempt to continue")
    attempt = attempts[-1]
    if (
        attempt.get("project") != str(Path(project).resolve())
        or attempt.get("run_id") != manifest["run_id"]
        or attempt.get("policy_profile") != "local-pilot-v1"
        or not isinstance(attempt.get("module"), str)
    ):
        raise HostStop("EXECUTION_COORDINATES", "attempt is not active local-pilot execution state")
    module_root = (project / Path(attempt["module"])).resolve()
    try:
        module_root.relative_to(project)
    except ValueError as error:
        raise HostStop("EXECUTION_COORDINATES", "attempt module escapes project") from error
    if not module_root.is_dir():
        raise HostStop("EXECUTION_COORDINATES", "attempt module root is unavailable")
    return {"run_root": root, "manifest": manifest, "authorization": authorization, "attempt": attempt, "module_root": module_root}


def _revalidate_execution_baseline(coordinates: Mapping[str, Any], request: Any) -> None:
    """Reject any non-delta baseline drift before the execution-start gate; the stop names the changed paths."""
    from tools.project_inventory import InventoryError

    try:
        project = Path(str(coordinates["manifest"]["project"])).resolve()
        root, attempt, module_root = Path(coordinates["run_root"]), coordinates["attempt"], Path(coordinates["module_root"])
        result = _baseline_check(project, root, attempt, module_root, request)
    except (InventoryError, KeyError, TypeError, ValueError, OSError) as error:
        executable = Path(str(getattr(request, "executable", "") or ""))
        if str(executable) not in {"", "."} and not executable.is_file():  # name the vanished runtime
            raise HostStop("BASELINE_INCOMPLETE", f"среда запуска модуля недоступна: {executable}; верните её или начните "
                           "новый прогон (команда — в status: drift)") from error
        raise HostStop("BASELINE_INCOMPLETE", "execution baseline is unreadable or incomplete") from error
    if result.get("status") != "UNCHANGED":
        report = _drift_report(project, coordinates["manifest"], result.get("changed") or [])
        changed = ", ".join(report["changed"]) or "входы baseline"
        raise HostStop(str(result.get("reason_code") or "BASELINE_DRIFT"),
                       f"файлы проекта изменились после scan: {changed}; {report['fix']}", report["fix"])


def _reject_prestart_execution(
    coordinates: Mapping[str, Any],
    reason: str,
    message: str,
    *,
    request: Any = None,
    automation_artifact: Mapping[str, Any] | None = None,
    autotest_review: Mapping[str, Any] | None = None,
) -> int:
    """Persist an attempt-bound pre-process rejection from durable facts only."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline
    from tools.execution_core import (
        runner_report as _report,
        request_is_attempt_local as _request_is_attempt_local,
        request_report_path as _request_report_path,
    )

    try:
        root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
        attempt_id = str(attempt["attempt_id"])
        project = Path(str(attempt["project"])).resolve(strict=True)
        module_name = str(attempt["module"])
        module_root = (project / module_name).resolve(strict=True)
        module_root.relative_to(project)
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        delta = read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
        effective = read_effective_canonical(root, attempt_id)
        document = effective["document"]
        adapter_id = baseline["adapter_id"]
        language = "python" if adapter_id == PYTEST else "java" if adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} else None
        if language is None or delta.get("baseline_digest") != attempt["baseline_digest"]:
            raise ValueError("durable execution facts are invalid")
        source = {
            "document_id": document["document_id"],
            "revision": document["revision"],
            "source_digest": effective["document_digest"],
            "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
        }
        exact_request = request if (
            _request_is_attempt_local(request, project, module_root)
            and _request_report_path(request) is not None
        ) else None
        report = _report(
            "NOT_RUNNABLE", module_root, language, source,
            str(delta["automation_digest"]), str(delta["review_digest"]),
            diagnostics=[{"path": "/execution", "code": reason, "message": message}],
            request=exact_request, run_root=root, attempt_id=attempt_id, ran_at=_attempt_time(root, attempt_id),
        )
        _publish_result_receipt(coordinates, report)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        _print({
            "status": "error", "reason": "EXECUTION_RECEIPT",
            "message": "pre-start rejection could not be persisted as an execution receipt",
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    if not isinstance(automation_artifact, Mapping) or not isinstance(autotest_review, Mapping):
        _print({
            "status": "error", "reason": "RUNNER_INPUT", "message": "execution carriers are unavailable",
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    try:
        from tools.finalize_attempt import finalize_durable_execution_attempt

        closed = finalize_durable_execution_attempt(
            Path(coordinates["run_root"]), attempt_id,
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        _print({
            "status": "error", "reason": "FINALIZATION", "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    _print({
        "status": "error", "reason": reason, "message": message,
        "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": "NOT_RUNNABLE",
        "accepted": closed["result"]["accepted"], "finalization": "complete",
    })
    return int(closed["exit_code"])


_NOT_PREPARED_FIX = ("materialize кладёт в проект только принятую автоматизацию: заверши статическое ревью автотестов "
                     "(autotest-reviewer, вердикт ПРИНЯТО), затем снова materialize")


# after a review that did not accept the automation — never push for another review past the limit
_REFUSED_FIX = ("статическое ревью не приняло автоматизацию, ревизий больше нет: закройте попытку командой finish — "
                "она напечатает next_run, команду следующего захода")


_REVISION_FIX = "ревью вернуло автоматизацию на правку: следующая ревизия tc-to-autotest:r2, её ревью autotest-reviewer:r2, затем materialize"


# the review accepted the automation, but materialize cannot write it into the project — no later step changes that
_UNMATERIALIZABLE_FIX = ("ревью приняло автоматизацию, но materialize не может записать её в проект (причина в message): закройте попытку "
                         "командой finish — она закроет её отрицательно (AUTOMATION_NOT_MATERIALIZABLE) и напечатает next_run")


def _not_prepared_fix(root: Path, attempt_id: str) -> str:
    from tools.finalize_attempt import automation_next

    try:
        outcome, detail = automation_next(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return _NOT_PREPARED_FIX
    if outcome == "PENDING":
        return _REVISION_FIX if detail == "tc-to-autotest:r2" else (
            f"автоматизация не закончена: следующий шаг — {detail}, затем ревью автотестов (ПРИНЯТО) и снова materialize")
    return {"AUTOMATION_REJECTED": _REFUSED_FIX, "AUTOMATION_BLOCKED": _REFUSED_FIX,
            "AUTOMATION_NOT_MATERIALIZABLE": _UNMATERIALIZABLE_FIX}.get(outcome, _NOT_PREPARED_FIX)


def _prepare_execution(coordinates: Mapping[str, Any]) -> list[str]:
    """Before the first run, materialize the accepted reviewed automation of the attempt, if not done yet.

    Inside the materialize step, not a driver: the same ``materialize_delta`` the agent would call, from the journal's own
    tc-to-autotest:rN and autotest-reviewer:rN answers.  Its checks stay as they are; a repeat is a no-op.
    """
    from tools.generated_delta import GeneratedDeltaError, journal_automation, materialize_delta
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline

    root = Path(coordinates["run_root"])
    attempt = coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    try:
        read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
        return []
    except (KeyError, OSError, TypeError, ValueError):
        pass
    inputs = journal_automation(root, attempt_id)
    if inputs is None:
        raise HostStop("EXECUTION_NOT_PREPARED", "no static review of the automation in this attempt", _not_prepared_fix(root, attempt_id))
    try:
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        document = read_effective_canonical(root, attempt_id)["document"]
        materialize_delta(
            Path(str(coordinates["manifest"]["project"])), Path(coordinates["module_root"]), baseline, inputs["automation"], inputs["review"],
            canonical_document=document, run_root=root, attempt_id=attempt_id,
            automation_history=inputs["automation_history"], review_history=inputs["review_history"],
        )
    except (GeneratedDeltaError, KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_NOT_PREPARED", str(error), _prepare_failure_fix(root, attempt_id, error)) from error
    return ["execution-inputs", "generated-delta"]


# the accepted automation passed the precheck, then its preparation failed
_PREPARE_IO_FIX = ("подготовка принятой автоматизации прервалась на вводе-выводе (причина в message): снова вызови materialize — "
                   "ответы и журнал те же, повтор безопасен")


_PREPARE_FINAL_FIX = ("подготовка принятой автоматизации отказала после записанных входов исполнения, и повтор materialize ответит так же: "
                      "закрой попытку командой finish --abandon AUTOMATION_NOT_MATERIALIZABLE — она закроет её отрицательно "
                      "и напечатает next_run")


def _prepare_failure_fix(root: Path, attempt_id: str, error: BaseException) -> str:
    from tools.finalize_attempt import automation_next
    from tools.execution_core import accepted_unprepared as _accepted_unprepared
    from tools.pilot_state import read_run

    try:
        outcome, _detail = automation_next(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return _NOT_PREPARED_FIX
    if outcome != "ACCEPTED":
        return _not_prepared_fix(root, attempt_id)
    if isinstance(error, OSError) or isinstance(error.__cause__, OSError) or not _accepted_unprepared(root, attempt_id):
        return _PREPARE_IO_FIX
    return (_PREPARE_FINAL_FIX.replace("finish --abandon", f'"{sys.executable}" -m tools.run_pipeline finish --project '
                                       f'"{read_run(root)["manifest"]["project"]}" --run {root.name} --abandon'))


# ---- the build is run by a human ----------------------------------------------------------------------------------
#
# ``materialize`` puts the accepted tests into the project and prints the command; ``ingest`` takes the reports of that run.  The package
# starts no process, opens no connection and passes no environment: what it holds against a report is the attempt it belongs to — the
# module, the reviewed tests, the files as they were written, the reports that lay there before, and a word (the challenge) the command
# carries into the report.  It cannot prove that the command was run; the result says so (``execution_source``).

_INGEST_REASONS = {"BUILD_FAILED": "NOT_RUNNABLE", "TESTS_NOT_STARTED": "NOT_RUNNABLE", "RESULT_UNAVAILABLE": "UNKNOWN"}
HUMAN_RESULT_NOTE = "результат запуска предоставлен человеком, пакет его не воспроизводил"
_REVIEW_NOTICE = ("код этих тестов написала модель и ещё не запускала: просмотрите перечисленные файлы до запуска; не правьте и не "
                  "форматируйте их после materialize — напечатанная команда уже отключает проверки стиля, а правка файла даст SOURCE_CHANGED. "
                  "Что проверить до запуска и как откатить — в HOW-TO-USE-MODE-B.md пакета")


class _NotBuilt(Exception):
    def __init__(self, code: str, message: str, request: Any = None):
        super().__init__(message)
        self.code, self.message, self.request = code, message, request


def _terminal_answer(coordinates: Mapping[str, Any], stage: str) -> int | None:
    """The answer of a command run again on an attempt that is closed: its result, exit code of the result."""
    from tools.pilot_state import exit_code, read_terminal_result

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    try:
        terminal = read_terminal_result(root, attempt_id)
    except KeyError:
        return None
    _print({
        "status": "ok", "stage": stage, "run_id": coordinates["manifest"]["run_id"], "attempt_id": attempt_id,
        "verdict": terminal.get("verification"), "accepted": terminal.get("accepted"), "finalization": "complete",
        **_quarantine_summary(root, attempt_id),
        **_style_notes(Path(coordinates["manifest"]["project"]), root, attempt_id),
        **_report_mismatch(root, attempt_id),
        **_automation_split(root, attempt_id),
    })
    return exit_code(terminal)


def _durable_carriers(coordinates: Mapping[str, Any]) -> dict[str, Any]:
    """The six carriers of an execution, from the attempt's durable evidence (as ``ingest`` reads them)."""
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical, read_execution_inputs

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    effective = read_effective_canonical(root, attempt_id)
    inputs = read_execution_inputs(root, attempt_id)
    delta = read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]
    revision = inputs["autotest_review"]["artifacts"]["autotest_review"]["automation_revision"]
    isolation = read_attempt_receipt(root, attempt_id, f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"]
    return {"document": effective["document"], "automation": inputs["automation_artifact"], "review": inputs["autotest_review"],
            "authorization": coordinates["authorization"], "isolation": isolation, "delta": delta}


def _closed_request(project: Path, coordinates: Mapping[str, Any], args: Any, carriers: Mapping[str, Any]) -> tuple[Any, str, dict[str, Any], Path]:
    """The closed request of the attempt, admitted before anything is written or run."""
    from tools.run_tests import build_closed_execution_request, validate_execution_eligibility

    try:
        execution_root, language, module = _resolve_host_context(project, getattr(args, "module", None), getattr(args, "language", None))
        if execution_root.resolve() != Path(coordinates["module_root"]).resolve():
            raise HostStop("EXECUTION_COORDINATES", "selected module differs from the attempt module")
        coordinates = {**coordinates, "module": module}
        request, _compatibility = build_closed_execution_request(execution_root, language, module, carriers["document"], carriers["automation"])
    except HostStop as error:
        raise _NotBuilt(error.code, str(error)) from error
    except (ValueError, TypeError, OSError) as error:
        raise _NotBuilt(getattr(error, "code", "RUNNER_INPUT"), str(error)) from error
    try:
        _revalidate_execution_baseline(coordinates, request)
        eligibility = validate_execution_eligibility(
            request, carriers["authorization"], carriers["automation"], carriers["review"], generated_delta_receipt=carriers["delta"],
            run_root=Path(coordinates["run_root"]), attempt_id=str(coordinates["attempt"]["attempt_id"]),
        )
    except HostStop as error:
        raise _NotBuilt(error.code, str(error), request) from error
    except (ValueError, TypeError, OSError) as error:
        raise _NotBuilt("BASELINE_INCOMPLETE", str(error), request) from error
    if eligibility.get("ready") is not True:
        raise _NotBuilt(str(eligibility.get("reason_code", "RUNNER_INPUT")), "execution admission is not ready", request)
    return request, language, coordinates, execution_root


def _durable_request(coordinates: Mapping[str, Any], carriers: Mapping[str, Any]) -> tuple[Any, str]:
    """The closed request of the attempt from what the journal and the baseline say, without a look at the test files — for the statement of
    a human who ran nothing, which must not depend on files the human may have touched."""
    from tools.automation_validation import required_symbol_pairs
    from tools.execution_adapters import ExecutionRequest, PYTEST, SYSTEM_MAVEN, command_for
    from tools.project_inventory import module_runtime_path, read_execution_baseline, system_maven_path

    root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
    module_root = Path(coordinates["module_root"]).resolve()
    try:
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        automation, document = carriers["automation"], carriers["document"]
        artifacts = automation["artifacts"]
        files = {row["file_id"]: row for row in artifacts["generated_files"]}
        symbols = {(row["file_id"], row["symbol_id"]): row for row in artifacts["generated_symbols"]}
        selectors: list[str] = []
        for pair in sorted(required_symbol_pairs(dict(automation), dict(document))):
            locator = symbols[pair]["locator"]
            if baseline["adapter_id"] == PYTEST:
                node = locator["function_name"] if locator["kind"] == "python_module_function" else "::".join([*locator["qualified_class_name"].split("."), locator["method_name"]])
                selectors.append(f"{files[pair[0]]['path']}::{node}")
            else:
                selectors.append(f"{locator['class_fqn']}#{locator['method_name']}")
        adapter_id = baseline["adapter_id"]
        runtime_path = Path(baseline["interpreter_path"] if adapter_id == PYTEST else baseline["executable_path"] if adapter_id == SYSTEM_MAVEN else baseline["wrapper_path"])
        executable = str(system_maven_path(str(runtime_path)) if adapter_id == SYSTEM_MAVEN else module_runtime_path(module_root, runtime_path.as_posix()))
        profile = str(baseline["build_profile"])
        typed = tuple(sorted((str(key), str(value)) for key, value in baseline["adapter_parameters"].items()))
        reports, argv = command_for(adapter_id, executable, profile, selectors)
        request = ExecutionRequest(adapter_id, executable, tuple(argv), str(module_root), tuple(selectors), 600, reports, ("PROJECT_NATIVE_ENV",), profile, typed)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_COORDINATES", f"the closed request of the attempt cannot be rebuilt: {error}") from error
    return request, "python" if adapter_id == PYTEST else "java"


def cmd_exec(args: Any) -> int:
    """``exec`` started the build itself; now a human runs it.  The answer names the way out and nothing is read, written or started."""
    _print({"status": "error", "stage": "exec", "reason": "EXEC_REPLACED",
            "message": "exec больше не запускает сборку: тесты кладёт materialize, команду запускает человек, отчёты принимает ingest",
            "fix": "python -m tools.run_pipeline materialize --project <project> --run <run_id> → человек запускает напечатанную команду → "
                   "python -m tools.run_pipeline ingest --project <project> --run <run_id> --reports <каталог с отчётами>"})
    return 2


def _challenge(request: Any, nonce: str) -> dict[str, Any]:
    """The word the printed command carries into the report: a system property of the test JVM (Maven), the name of the suite (pytest)."""
    from tools.execution_adapters import MAVEN, PYTEST, SYSTEM_MAVEN

    if request.adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return {"kind": "system-property", "name": "pilot.run", "value": nonce}
    if request.adapter_id == PYTEST:
        return {"kind": "suite-name", "name": "junit_suite_name", "value": "PILOT-" + nonce}
    return {"kind": "not_available", "name": None, "value": None}  # Gradle does not hand a -D of the build to the test JVM


def _relative_executable(executable: str, base: Path) -> str:
    try:
        return Path(os.path.relpath(executable, base)).as_posix()
    except ValueError:
        return executable


def _human_command(project: Path, module_root: Path, module_relative: str, request: Any, challenge: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The directory and the argument list a human runs: the closed request, from the root of the reactor for a Maven module of a
    multi-module project (``-pl <module> -am``), with the challenge in it."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    argv = list(request.argv)
    if request.adapter_id in {MAVEN, SYSTEM_MAVEN}:
        from_reactor = module_relative not in {"", "."}
        base = project if from_reactor else module_root
        executable = request.executable if request.adapter_id == SYSTEM_MAVEN else _relative_executable(request.executable, base)
        extra = ["-pl", module_relative, "-am", "-Dsurefire.failIfNoSpecifiedTests=false", "-DfailIfNoTests=false"] if from_reactor else []
        return ("." if from_reactor else module_relative), [executable, *argv[1:-1], *extra, f"-Dpilot.run={challenge['value']}", argv[-1]]
    if request.adapter_id == PYTEST:
        index = argv.index("--junitxml") + 2
        return module_relative, [_relative_executable(request.executable, module_root), *argv[1:index], "-o", f"junit_suite_name={challenge['value']}", *argv[index:]]
    assert request.adapter_id == GRADLE
    return module_relative, [_relative_executable(request.executable, module_root), *argv[1:]]


def command_text(tokens: Sequence[str]) -> str:
    return " ".join(command_token(token) for token in tokens)


def command_text_powershell(tokens: Sequence[str]) -> str:
    """The same tokens for PowerShell: ``& `` and every token in double quotes.  A line that starts with a quoted token is an expression there, not a
    call; Windows PowerShell 5.1 splits a bare ``-Dfoo.bar=baz`` at the dot and reads a comma as an array.  The characters that PowerShell expands in
    double quotes (``$`` and the back quote) are among those ``command_token`` refuses."""
    for token in tokens:
        command_token(token)
    return "& " + " ".join('"' + token + '"' for token in tokens)


def printed_forms(project: Path, working: str, command: Sequence[str]) -> tuple[str, str]:
    """The command as printed for cmd.exe/sh and for PowerShell.  A wrapper of the module (``mvnw.cmd``, ``gradlew``) is named by its absolute path:
    PowerShell never starts a program of the current directory by its bare name, nor do cmd.exe with NoDefaultCurrentDirectoryInExePath and sh.
    The array ``command`` that the receipt binds stays as it is."""
    first = str(command[0])
    wrapper = os.path.normpath(os.path.join(project, working, first))
    printed = [wrapper, *command[1:]] if not os.path.isabs(first) and os.path.isfile(wrapper) else list(command)
    return command_text(printed), command_text_powershell(printed)


def _command_line(tokens: Sequence[str]) -> str:
    """The command for a refusal's fix: its text, or the note that it cannot be printed."""
    try:
        return command_text(tokens)
    except HostStop:
        return "(команда не печатается: путь в ней нельзя записать безопасно, см. ответ materialize)"


def _link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)  # a symbolic link or a Windows reparse point


def _report_files(location: Path, allowed: Sequence[Path]) -> list[tuple[Path, os.stat_result]]:
    """The XML files under ``location`` — which has to lie inside one of ``allowed`` with no link on the way and holds no link or special
    file; (path, lstat) in path order.  A missing location is an empty list."""
    location = Path(os.path.abspath(location))
    base = next((root for root in allowed if location == root or root in location.parents), None)
    if base is None:
        raise HostStop("INGEST_REPORT_OUTSIDE_MODULE", f"каталог отчётов {location} лежит вне модуля и вне каталога incoming прогона")
    walk = base
    for part in location.relative_to(base).parts:
        walk = walk / part
        if os.path.lexists(walk) and _link(walk):
            raise HostStop("INGEST_REPORT_OUTSIDE_MODULE", f"на пути к отчётам {walk} — ссылка; пакет по ссылкам отчёты не читает")
    if _link(base) if os.path.lexists(base) else False:
        raise HostStop("INGEST_REPORT_OUTSIDE_MODULE", f"{base} — ссылка")
    if not os.path.lexists(location):
        return []
    found: list[tuple[Path, os.stat_result]] = []
    candidates = [location] if location.is_file() else []
    if location.is_dir():
        for directory, names, files in os.walk(location, followlinks=False):
            for name in [*names, *files]:
                if _link(Path(directory) / name):
                    raise HostStop("INGEST_REPORT_OUTSIDE_MODULE", f"в каталоге отчётов ссылка: {Path(directory) / name}")
            candidates.extend(Path(directory) / name for name in files)
    for path in sorted(candidates):
        if path.suffix.lower() != ".xml":
            continue
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise HostStop("INGEST_REPORT_OUTSIDE_MODULE", f"{path} — не обычный файл")
        found.append((path, info))
    return found


def _file_fingerprint(path: Path, info: os.stat_result) -> tuple[int, int, str]:
    from tools.run_tests import NATIVE_REPORT_MAX_BYTES

    digest = "oversized"
    if info.st_size <= NATIVE_REPORT_MAX_BYTES:
        with path.open("rb") as stream:
            digest = hashlib.sha256(stream.read(NATIVE_REPORT_MAX_BYTES + 1)).hexdigest()
    return info.st_mtime_ns, info.st_size, digest


def _inventory_rows(project: Path, entries: Sequence[tuple[Path, os.stat_result]]) -> list[dict[str, Any]]:
    rows = []
    for path, info in entries:
        mtime_ns, size, digest = _file_fingerprint(path, info)
        rows.append({"path": path.relative_to(project).as_posix(), "mtime_ns": mtime_ns, "size": size, "sha256": digest})
    return rows


def _read_printed(root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    from tools.pilot_state import read_attempt_receipt

    try:
        return read_attempt_receipt(root, attempt_id, "materialization-command", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, ValueError, OSError):
        return None


def _changed_files(coordinates: Mapping[str, Any], printed: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The files of ``materialize`` that are not as it wrote them: (path from the project, the digest it wrote)."""
    module_root, project = Path(coordinates["module_root"]), Path(coordinates["manifest"]["project"])
    changed = []
    for row in printed["files"]:
        target = module_root / Path(row["path"])
        try:
            current = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            current = None
        if current != row["content_digest"]:
            changed.append((target.relative_to(project).as_posix(), row["content_digest"]))
    return changed


def _restore_files(coordinates: Mapping[str, Any], printed: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """``materialize --restore``: write again, from the reviewed answer of the journal, each file that is not as materialize wrote it —
    only when the bytes of the answer are the digest materialize recorded.  (restored, not restorable)"""
    from tools.generated_delta import journal_automation

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    module_root, project = Path(coordinates["module_root"]).resolve(), Path(coordinates["manifest"]["project"])
    inputs = journal_automation(root, attempt_id)
    answered = {row["path"]: row for row in inputs["automation"]["artifacts"]["generated_files"]} if inputs else {}
    wanted = {path: digest for path, digest in _changed_files(coordinates, printed)}
    restored, stuck = [], []
    for row in printed["files"]:
        target = module_root / Path(row["path"])
        shown = target.relative_to(project).as_posix() if target.is_relative_to(project) else str(target)
        if shown not in wanted:
            continue
        content = answered.get(row["path"], {}).get("content")
        data = content.encode("utf-8") if isinstance(content, str) else None
        safe = target.resolve().is_relative_to(module_root) and not (target.exists() and _link(target)) and target.parent.is_dir() and not _link(target.parent)
        if data is None or not safe or "sha256:" + hashlib.sha256(data).hexdigest() != row["content_digest"]:
            stuck.append(shown)
            continue
        target.write_bytes(data)
        restored.append(shown)
    return restored, stuck


def _materialize_answer(project: Path, coordinates: Mapping[str, Any], printed: Mapping[str, Any], *, repeated: bool, restored: Sequence[str] = (), stuck: Sequence[str] = ()) -> int:
    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    module_root = Path(coordinates["module_root"])
    module_relative = str(coordinates["attempt"]["module"])
    changed = []
    for row in printed["files"]:
        try:
            current = "sha256:" + hashlib.sha256((module_root / Path(row["path"])).read_bytes()).hexdigest()
        except OSError:
            current = None
        if current != row["content_digest"]:
            changed.append((module_root / Path(row["path"])).relative_to(project).as_posix())
    command = printed["command"]
    working = printed["working_directory"]
    try:
        shown, shown_powershell = printed_forms(project, working, command)
        tail = (f'--run {root.name} --reports <каталог с отчётами внутри модуля '
                f'или {(root / "incoming").relative_to(project).as_posix()}/>')
        ingest = f'{command_text([sys.executable, "-m", "tools.run_pipeline", "ingest", "--project", str(project)])} {tail}'
        ingest_powershell = f'{command_text_powershell([sys.executable, "-m", "tools.run_pipeline", "ingest", "--project", str(project)])} {tail}'
    except HostStop as stop:
        _print({"status": "error", "stage": "materialize", "reason": stop.code, "message": str(stop), "fix": stop.fix, "attempt_id": attempt_id})
        return 2
    _print({
        "status": "ok", "stage": "materialize", "run_id": coordinates["manifest"]["run_id"], "attempt_id": attempt_id,
        "repeated": repeated, "working_directory": working, "command": command,
        "command_text": shown, "command_text_powershell": shown_powershell,
        "report_location": printed["report_location"], "challenge": printed["challenge"]["kind"],
        "files_to_review": [(module_root / Path(row["path"])).relative_to(project).as_posix() for row in printed["files"]],
        **({"files_changed_since_materialize": changed} if changed else {}),
        **({"files_restored": list(restored)} if restored else {}),
        **({"files_not_restorable": list(stuck)} if stuck else {}),
        "review_required": True, "note": _REVIEW_NOTICE,
        "service_note": "если у модуля есть стенд (service_url_env), поднимите его до запуска; пакет к нему не обращается",
        "next": ingest, "next_powershell": ingest_powershell,
    })
    return 0


def cmd_materialize(args: Any) -> int:
    """The accepted tests into the project, then the command a human runs — nothing is started."""
    from tools.execution_adapters import request_digest

    project = Path(args.project).resolve()
    try:
        coordinates = _validated_execution_coordinates(project, args.run)
    except HostStop as error:
        _print({"status": "error", "stage": "materialize", "reason": error.code, "message": str(error)})
        return 2
    answered = _terminal_answer(coordinates, "materialize")
    if answered is not None:
        return answered
    root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    printed = _read_printed(root, attempt_id)
    if printed is not None:
        restored, stuck = _restore_files(coordinates, printed) if getattr(args, "restore", False) else ([], [])
        return _materialize_answer(project, coordinates, printed, repeated=True, restored=restored, stuck=stuck)
    if getattr(args, "restore", False):
        _print({"status": "error", "stage": "materialize", "reason": "MATERIALIZE_RESTORE_NOTHING", "message": "тесты ещё не положены в проект: восстанавливать нечего",
                "fix": "materialize без --restore", "attempt_id": attempt_id})
        return 2
    try:
        command_text([sys.executable, str(project), str(attempt["module"])])  # what the printed lines will hold, before anything is written
        _prepare_execution(coordinates)
    except HostStop as stop:
        _print({"status": "error", "stage": "materialize", "reason": stop.code, "message": str(stop), **({"fix": stop.fix} if stop.fix else {}),
                "attempt_id": attempt_id})
        return 2
    try:
        carriers = _durable_carriers(coordinates)
    except (HostStop, KeyError, ValueError, TypeError, OSError) as error:
        _print({"status": "error", "stage": "materialize", "reason": getattr(error, "code", "RUNNER_INPUT"), "message": str(error), "attempt_id": attempt_id})
        return 2
    try:
        request, language, coordinates, execution_root = _closed_request(project, coordinates, args, carriers)
    except _NotBuilt as error:
        return _reject_prestart_execution(coordinates, error.code, error.message, request=error.request,
                                          automation_artifact=carriers["automation"], autotest_review=carriers["review"])
    delta = carriers["delta"]["delta"]
    digest = request_digest(request)
    nonce = hashlib.sha256(f"{attempt_id}\n{delta['digest']}\n{digest}".encode("utf-8")).hexdigest()[:16]
    challenge = _challenge(request, nonce)
    module_relative = str(attempt["module"])
    working, command = _human_command(project, Path(coordinates["module_root"]), module_relative, request, challenge)
    try:
        command_text([working, *command])  # a command that cannot be printed is not published as one
    except HostStop as stop:
        _print({"status": "error", "stage": "materialize", "reason": stop.code, "message": str(stop), "fix": stop.fix, "attempt_id": attempt_id})
        return 2
    taken = time.time_ns()  # before the look at the reports: a file touched after this has a later time than this
    entries = _report_files(Path(coordinates["module_root"]) / request.report_paths[0], [Path(coordinates["module_root"])])
    facts = {
        "baseline_digest": attempt["baseline_digest"], "generated_delta_digest": delta["digest"], "execution_request_digest": digest,
        "language": language, "module": module_relative, "working_directory": working, "command": command,
        "command_digest": _canonical_digest({"working_directory": working, "command": command}),
        "report_location": request.report_paths[0], "inventory_taken_ns": taken, "report_inventory": _inventory_rows(project, entries), "challenge": challenge,
        "files": [{"path": row["path"], "content_digest": row["content_digest"]} for row in delta["files"] if row.get("materialization") == "MATERIALIZED"],
    }
    try:
        published = _publish_fixed_receipt_resuming(root, attempt_id, "materialization-command", facts)
    except (KeyError, TypeError, ValueError, OSError) as error:
        _print({"status": "error", "stage": "materialize", "reason": getattr(error, "code", "MATERIALIZATION_COMMAND_UNBOUND"), "message": str(error), "attempt_id": attempt_id})
        return 2
    return _materialize_answer(project, coordinates, published["record"], repeated=False)


def _challenge_problem(challenge: Mapping[str, Any], snapshots: Mapping[Path, bytes]) -> str | None:
    """INGEST_CHALLENGE_MISSING / _MISMATCH when a report does not carry the word of the printed command (on the bytes as written, before
    the durable copy drops everything but names and counts)."""
    kind = challenge["kind"]
    if kind == "not_available":
        return None
    for content in snapshots.values():
        try:
            tree = ET.fromstring(content)
        except ET.ParseError:
            return None  # a malformed report is the mismatch check's refusal
        if kind == "system-property":
            seen = [item.get("value") for item in tree.iter("property") if item.get("name") == challenge["name"]]
            other = bool(seen)
        else:
            seen = [item.get("name") for item in tree.iter("testsuite")]
            other = any(str(name).startswith("PILOT-") for name in seen)
        if challenge["value"] in seen:
            continue
        return "INGEST_CHALLENGE_MISMATCH" if other else "INGEST_CHALLENGE_MISSING"
    return None


def _ran_at_and_duration(snapshots: Mapping[Path, bytes], entries: Sequence[tuple[Path, os.stat_result]]) -> tuple[str, float]:
    """The time of the run and its length from the reports alone: the newest ``timestamp`` of a suite (pytest), else the newest report
    file; the sum of the ``time`` of the test cases."""
    stamps: list[datetime] = []
    duration = 0.0
    for content in snapshots.values():
        try:
            tree = ET.fromstring(content)
        except ET.ParseError:
            continue
        for suite in tree.iter("testsuite"):
            try:
                parsed = datetime.fromisoformat(suite.get("timestamp", ""))
            except ValueError:
                continue
            stamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
        for case in tree.iter("testcase"):
            try:
                duration += float(case.get("time", 0) or 0)
            except ValueError:
                pass
    if stamps:
        return max(stamps).isoformat(), round(duration, 3)
    newest = max((info.st_mtime_ns for _path, info in entries), default=0)
    return datetime.fromtimestamp(newest / 1_000_000_000, timezone.utc).isoformat(), round(duration, 3)


def _ingest_refusal(attempt_id: str, code: str, message: str, fix: str, **extra: Any) -> int:
    _print({"status": "error", "stage": "ingest", "reason": code, "message": message, "fix": fix, "attempt_id": attempt_id, **extra})
    return 2


def _publish_result_receipt(coordinates: Mapping[str, Any], report: Mapping[str, Any]) -> Mapping[str, Any]:
    """The result of the attempt as a receipt: a pure function of the report, installed with the events it lacks (the same bytes) or refused
    (other bytes) — never bound from what lies on the disk."""
    from tools.pilot_state import derive_state, read_attempt_receipt, record_execution_unknown

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    try:
        published = _publish_fixed_receipt_resuming(root, attempt_id, "execution-receipt", {"payload": dict(report)})
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop(getattr(error, "code", "EXECUTION_RECEIPT"), f"the result receipt could not be installed: {error}") from error
    record = read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    if record.get("payload") != dict(report):
        raise HostStop("EXECUTION_RECEIPT", "execution receipt read-back does not equal the report")
    if report.get("verdict") == "UNKNOWN" and not any(
        event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_UNKNOWN" for event in derive_state(root)["events"]
    ):
        record_execution_unknown(root, attempt_id, str(record["digest"]))
    return published


def _record_late_drift(coordinates: Mapping[str, Any], request: Any, report: Mapping[str, Any]) -> None:
    """A result recorded by an ingest that was cut before the attempt was closed: if the project or the runtime moved since, that is said once
    (``resume-validation``) and the attempt closes as not accepted (BASELINE_DRIFT, SKILLSRC_DRIFT) — never as the PASS that was recorded.  Not
    asked again once the finalization was installed or the drift recorded; a rejection before the start (NOT_RUNNABLE) has nothing to hold."""
    from tools.pilot_state import publish_resume_validation, read_closure_artifact_if_present, read_resume_validation_if_present

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    if report.get("verdict") == "NOT_RUNNABLE":
        return
    if read_closure_artifact_if_present(root, attempt_id, "finalization_receipt") is not None or read_resume_validation_if_present(root, attempt_id) is not None:
        return
    try:
        _revalidate_execution_baseline(coordinates, request)
    except HostStop as drift:
        publish_resume_validation(root, attempt_id, drift.code)


def _finish_ingest(coordinates: Mapping[str, Any], report: Mapping[str, Any]) -> int:
    from tools.finalize_attempt import finalize_durable_execution_attempt

    root, attempt_id = Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])
    try:
        closed = finalize_durable_execution_attempt(root, attempt_id)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        _print({"status": "error", "stage": "ingest", "reason": "FINALIZATION", "message": str(error), "attempt_id": attempt_id})
        return 2
    result = closed["result"]
    source = report.get("execution_source") or {}
    _print({
        "status": "ok", "stage": "ingest", "run_id": coordinates["manifest"]["run_id"], "attempt_id": attempt_id,
        "verdict": result.get("verification"), "accepted": result.get("accepted"), "finalization": "complete",
        "execution_source": {**source, "note": HUMAN_RESULT_NOTE} if source else None,
        **_quarantine_summary(root, attempt_id),
        **_style_notes(Path(coordinates["manifest"]["project"]), root, attempt_id),
        **_report_mismatch(root, attempt_id),
        **_automation_split(root, attempt_id),
    })
    return int(closed["exit_code"])


def _ingest_statement(project: Path, coordinates: Mapping[str, Any], carriers: Mapping[str, Any], request: Any, printed: Mapping[str, Any],
                      reason: str, language: str) -> int:
    """``ingest --no-reports --reason …``: the human says the run left no reports.  Only a negative result can come of it: the tests were not
    run (BUILD_FAILED, TESTS_NOT_STARTED — NOT_RUNNABLE, the files taken out again) or their result is lost (RESULT_UNAVAILABLE — UNKNOWN,
    the files kept)."""
    from tools.execution_adapters import request_digest
    from tools.pilot_state import claim_execution_start, derive_state, read_effective_canonical

    root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    delta = carriers["delta"]["delta"]
    effective = read_effective_canonical(root, attempt_id)
    source = {
        "document_id": effective["document"]["document_id"], "revision": effective["document"]["revision"],
        "source_digest": effective["document_digest"], "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
    }
    execution_source = {"run_by": "human", "human_command_digest": printed["command_digest"], "challenge": "not_available", "statement": reason}
    verdict = _INGEST_REASONS[reason]
    module_root = Path(coordinates["module_root"])
    starts = [row for row in derive_state(root)["events"] if row.get("event_type") == "EXECUTION_STARTED" and row.get("attempt_id") == attempt_id]
    if starts and (verdict == "NOT_RUNNABLE" or len(starts) != 1 or starts[0].get("artifact_digest") != request_digest(request)):
        # an ingest that was cut after its claim left the start: it is the one of this request (RESULT_UNAVAILABLE goes on with it); a build that
        # "did not start" cannot follow a start, and a start of another request is not ours
        return _ingest_refusal(attempt_id, "INGEST_REASON_AFTER_RESULT", "исполнение этой попытки уже начато",
                               "приём отчётов уже начинался: повторите ingest с теми же отчётами или `ingest --no-reports --reason RESULT_UNAVAILABLE`")
    if verdict == "NOT_RUNNABLE":
        report = _core_report(
            "NOT_RUNNABLE", module_root, language, source, str(delta["automation_digest"]), str(delta["review_digest"]),
            diagnostics=[{"path": "/execution", "code": reason, "message": f"{HUMAN_RESULT_NOTE}: {reason}"}],
            request=request, run_root=root, attempt_id=attempt_id, execution_source=execution_source,
            ran_at=_printed_time(root, attempt_id),
        )
    else:
        if not starts:
            claim_execution_start(root, attempt_id, request_digest(request))
        run_id = "RUN-human-" + attempt_id
        lost = _core_process_row("JUNIT_MISSING", ProcessOutcome(0, "", "", "NOT_OBSERVED"), run_id, source, request.adapter_id, 0.0)
        report = _core_report(  # no report of the run: the same row a run without a report gets, its reason in the statement
            "UNKNOWN", module_root, language, source, str(delta["automation_digest"]), str(delta["review_digest"]),
            run_id=run_id, process_evidence=[lost], authoritative=False, exit_code=None, duration_sec=0.0, request=request,
            run_root=root, attempt_id=attempt_id, execution_source=execution_source, ran_at=_printed_time(root, attempt_id),
        )
    try:
        _publish_result_receipt(coordinates, report)
    except HostStop as error:
        return _ingest_refusal(attempt_id, error.code, str(error), "повторите ingest с теми же аргументами")
    return _finish_ingest(coordinates, report)


def _attempt_time(root: Path, attempt_id: str) -> str:
    """The time of the first event of the attempt: a rejection before the start is made again by the next command, and a receipt that carried
    the clock of the moment would then differ from the one already installed."""
    from tools.pilot_state import derive_state

    return next(str(row["observed_at"]) for row in derive_state(root)["events"] if row.get("attempt_id") == attempt_id and row.get("observed_at"))


def _printed_time(root: Path, attempt_id: str) -> str:
    """A time that is a fact of the attempt, not a clock: the modification time of the receipt of the printed command."""
    path = root / "materialization-commands" / f"{attempt_id}.json"
    return datetime.fromtimestamp(os.stat(path).st_mtime_ns / 1_000_000_000, timezone.utc).isoformat()


def cmd_ingest(args: Any) -> int:
    """The reports of a run a human made of the printed command, held against the attempt — or the human's statement that there are none."""
    from tools.execution_adapters import request_digest
    from tools.exec_runner import ingest_reports, report_set_problems
    from tools.pilot_state import claim_execution_start, read_attempt_receipt
    from tools.run_tests import validate_artifact_runner_compatibility

    project = Path(args.project).resolve()
    try:
        coordinates = _validated_execution_coordinates(project, args.run)
    except HostStop as error:
        _print({"status": "error", "stage": "ingest", "reason": error.code, "message": str(error)})
        return 2
    answered = _terminal_answer(coordinates, "ingest")
    if answered is not None:
        return answered
    root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    reports_given, no_reports, reason = getattr(args, "reports", None), bool(getattr(args, "no_reports", False)), getattr(args, "reason", None)
    if no_reports and reports_given:
        return _ingest_refusal(attempt_id, "INGEST_REASON_WITH_REPORTS", "--no-reports и --reports вместе", "оставьте одно: отчёты — --reports <каталог>, их отсутствие — --no-reports --reason …")
    if no_reports and reason not in _INGEST_REASONS or reason is not None and not no_reports:
        return _ingest_refusal(attempt_id, "INGEST_REASON_UNKNOWN", "причина без --no-reports или не из списка", "--no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE")
    printed = _read_printed(root, attempt_id)
    if printed is None:
        return _ingest_refusal(
            attempt_id, "INGEST_WITHOUT_MATERIALIZE", "команда запуска не напечатана: тесты ещё не положены в проект",
            f'"{sys.executable}" -m tools.run_pipeline materialize --project "{project}" --run {root.name}')
    existing = None
    try:
        existing = read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, ValueError, OSError):
        pass
    try:
        carriers = _durable_carriers(coordinates)
        if no_reports or existing is not None:  # a statement without reports, or a result already recorded, does not depend on the test files, which the human may have touched
            request, language = _durable_request(coordinates, carriers)
        else:
            request, language, coordinates, execution_root = _closed_request(project, coordinates, args, carriers)
    except _NotBuilt as error:
        if error.code == "RUNNER_COMPATIBILITY":
            changed = _changed_files(coordinates, printed)
            restore = f'"{sys.executable}" -m tools.run_pipeline materialize --project "{project}" --run {root.name} --restore'
            statement = f'"{sys.executable}" -m tools.run_pipeline ingest --project "{project}" --run {root.name} --no-reports --reason RESULT_UNAVAILABLE'
            return _ingest_refusal(
                attempt_id, "INGEST_TESTS_CHANGED", "файлы тестов не такие, как были при materialize: отчёт нельзя отнести к рецензированному коду; "
                + "; ".join(f"{path} (ожидался {digest})" for path, digest in changed),
                f"верните файлы командой {restore}, запустите команду из materialize снова и повторите ingest; "
                f"если вернуть нельзя — {statement} (файлы сохранятся)", files_changed=[path for path, _digest in changed])
        return _ingest_refusal(attempt_id, "INGEST_REQUEST_CHANGED", f"{error.code}: {error.message}", "верните файлы и окружение модуля, как были при materialize, или начните новый прогон")
    except (HostStop, KeyError, ValueError, TypeError, OSError) as error:
        return _ingest_refusal(attempt_id, getattr(error, "code", "RUNNER_INPUT"), str(error), "повторите команду; если не помогает — новый scan")
    digest = request_digest(request)
    if digest != printed["execution_request_digest"]:
        return _ingest_refusal(attempt_id, "INGEST_REQUEST_CHANGED", "закрытый запрос модуля стал другим, чем при materialize", "верните runtime модуля или повторите materialize")
    if existing is not None and no_reports:
        return _ingest_refusal(attempt_id, "INGEST_REASON_AFTER_RESULT", "результат попытки уже записан", "ingest без --no-reports печатает его снова")
    if no_reports:
        return _ingest_statement(project, coordinates, carriers, request, printed, str(reason), language)
    # the reports
    module_root = Path(coordinates["module_root"])
    location = Path(os.path.abspath(reports_given)) if reports_given else module_root / printed["report_location"]  # a relative path is the caller's
    try:
        entries = _report_files(location, [module_root, root / "incoming"])
    except HostStop as error:
        return _ingest_refusal(attempt_id, error.code, str(error), "скопируйте отчёты в каталог внутри модуля или в <run>/incoming/ и повторите ingest")
    before = {row["path"]: (row["mtime_ns"], row["size"], row["sha256"]) for row in printed["report_inventory"]}
    taken = int(printed["inventory_taken_ns"])
    # fresh: not as it lay when materialize looked, and not older than that look (a copy that kept an old time is an old report)
    fresh = [(path, info) for path, info in entries
             if before.get(path.relative_to(project).as_posix()) != _file_fingerprint(path, info) and info.st_mtime_ns >= taken]
    snapshots = _report_snapshots(tuple(path for path, _info in fresh))
    if existing is not None:
        if existing["payload"].get("execution_source", {}).get("statement") == "reports" and fresh:
            recorded = {row["digest"] for row in existing["payload"]["execution"]["report_evidence"]}
            if {row["digest"] for row in _report_digest_evidence(snapshots, module_root)} != recorded:
                return _ingest_refusal(attempt_id, "INGEST_REPORT_CHANGED", "отчёты в каталоге другие, чем те, по которым записан результат",
                                       "результат попытки записан и не меняется; для нового прогона — scan --new-run")
        try:
            _record_late_drift(coordinates, request, existing["payload"])
        except (HostStop, KeyError, TypeError, ValueError, OSError) as error:
            return _ingest_refusal(attempt_id, getattr(error, "code", "FINALIZATION"), str(error), "повторите ingest; если не помогает — новый scan")
        return _finish_ingest(coordinates, existing["payload"])
    command_text = _command_line(printed["command"])
    again = f"запустите команду из materialize (она напечатается снова: materialize --run {root.name}), затем повторите ingest"
    if not entries:
        return _ingest_refusal(attempt_id, "INGEST_REPORT_MISSING", f"в {location} нет XML-отчётов",
                               f"{again}; если сборка не дошла до тестов — ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED. Команда: {command_text}")
    if not fresh:
        return _ingest_refusal(attempt_id, "INGEST_REPORT_STALE", "все отчёты лежали здесь до materialize, не менялись или старше его (время изменения файла раньше materialize)",
                               f"{again}. Команда: {command_text}")
    problem = _challenge_problem(printed["challenge"], snapshots)
    if problem is not None:
        return _ingest_refusal(attempt_id, problem, "в отчёте нет слова напечатанной команды" if problem.endswith("MISSING") else "в отчёте слово другого запуска",
                               f"отчёт получен не командой из materialize этой попытки; {again}. Команда: {command_text}")
    compatibility = validate_artifact_runner_compatibility(execution_root.resolve(), language, carriers["document"], carriers["automation"])
    source = carriers["automation"]["artifacts"]["source"]
    problems = report_set_problems(snapshots, compatibility, source, language)
    if problems:
        return _ingest_refusal(attempt_id, "INGEST_REPORT_MISMATCH", "; ".join(problems[:3]), f"отчёты не те тесты, что рецензированы: {again}. Команда: {command_text}",
                               reports=[path.relative_to(project).as_posix() for path, _info in fresh][:10])
    ran_at, duration = _ran_at_and_duration(snapshots, fresh)
    run_id = "RUN-" + hashlib.sha256((digest + "\n" + "\n".join(sorted(row["digest"] for row in _report_digest_evidence(snapshots, module_root)))).encode("utf-8")).hexdigest()[:32]
    execution_source = {"run_by": "human", "human_command_digest": printed["command_digest"],
                        "challenge": "ok" if printed["challenge"]["kind"] != "not_available" else "not_available", "statement": "reports"}

    def on_execution_start(request_value: Any) -> None:
        _revalidate_execution_baseline(coordinates, request_value)
        claim_execution_start(root, attempt_id, request_digest(request_value))

    def post_execution_check() -> str | None:
        try:
            _revalidate_execution_baseline(coordinates, request)
        except HostStop as error:
            return error.code
        return None

    report = ingest_reports(
        request, carriers["authorization"], carriers["document"], carriers["automation"], carriers["review"],
        host_isolation_receipt=carriers["isolation"], generated_delta_receipt=carriers["delta"], run_root=root, attempt_id=attempt_id,
        snapshots=snapshots, execution_source=execution_source, run_id=run_id, ran_at=ran_at, duration_sec=duration,
        on_execution_start=on_execution_start, post_execution_check=post_execution_check,
    )
    try:
        _publish_result_receipt(coordinates, report)
    except HostStop as error:
        return _ingest_refusal(attempt_id, error.code, str(error), "повторите ingest с теми же отчётами; если файл результата с иными байтами уже лежит — новый прогон")
    return _finish_ingest(coordinates, report)


def add_parsers(sub: Any, shared: Any) -> None:
    """The parsers of ``exec`` (replaced), ``materialize`` and ``ingest`` — added by ``run_pipeline.build_parser`` when this module is in the package."""
    exec_parser = sub.add_parser("exec", parents=[shared], help="Replaced by materialize and ingest (P4b): answers with the way out and starts nothing.")
    exec_parser.add_argument("--run")
    exec_parser.add_argument("--language", choices=("python", "java"))
    exec_parser.add_argument("--executor", choices=("local",), default="local")
    for flag in ("--canonical-document", "--automation-artifact", "--autotest-review", "--authorization-receipt", "--host-isolation-receipt", "--generated-delta-receipt"):
        exec_parser.add_argument(flag)
    materialize_parser = sub.add_parser("materialize", parents=[shared], help="P4: put the accepted tests into the project and print the command a human runs.")
    materialize_parser.add_argument("--run", required=True)
    materialize_parser.add_argument("--language", choices=("python", "java"))
    materialize_parser.add_argument("--restore", action="store_true", help="write again the test files that are not as materialize wrote them (from the reviewed answer of the journal)")
    ingest_parser = sub.add_parser("ingest", parents=[shared], help="P4: take the reports of the human's run of the printed command.")
    ingest_parser.add_argument("--run", required=True)
    ingest_parser.add_argument("--language", choices=("python", "java"))
    ingest_parser.add_argument("--reports", help="a folder of the reports inside the module (default: where the build writes them) or inside <run>/incoming/")
    ingest_parser.add_argument("--no-reports", action="store_true", help="the run left no reports (needs --reason); only a negative result can come of it")
    ingest_parser.add_argument("--reason", choices=("BUILD_FAILED", "TESTS_NOT_STARTED", "RESULT_UNAVAILABLE"))



COMMANDS = {
    "materialize": cmd_materialize,
    "ingest": cmd_ingest,
    "exec": cmd_exec,
}
