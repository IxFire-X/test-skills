"""P8a-1: the commands that run the generated tests — ``exec`` and ``rerun-retained`` — and what only they use.

Moved out of ``run_pipeline`` unchanged; the core reaches this module only through ``tools.execution_gate``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.run_pipeline import HostStop
from tools.execution_core import (
    automation_split_fields as _automation_split,
    baseline_check as _baseline_check,
    digest_bytes as _digest_bytes,
    drift_report as _drift_report,
    print_payload as _print,
    read_run_coordinate as _read_run_coordinate,
    report_mismatch as _report_mismatch,
    style_notes as _style_notes,
)
from tools.exec_stand import service_stop as _service_stop


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
    """Reject any non-delta baseline drift before the execution-start gate; the stop names the changed paths (FaT-G)."""
    from tools.project_inventory import InventoryError

    try:
        project = Path(str(coordinates["manifest"]["project"])).resolve()
        root, attempt, module_root = Path(coordinates["run_root"]), coordinates["attempt"], Path(coordinates["module_root"])
        result = _baseline_check(project, root, attempt, module_root, request)
    except (InventoryError, KeyError, TypeError, ValueError, OSError) as error:
        executable = Path(str(getattr(request, "executable", "") or ""))
        if str(executable) not in {"", "."} and not executable.is_file():  # FaT-H2b 2.11: name the vanished runtime
            raise HostStop("BASELINE_INCOMPLETE", f"среда запуска модуля недоступна: {executable}; верните её или начните "
                           "новый прогон (команда — в status: drift)") from error
        raise HostStop("BASELINE_INCOMPLETE", "execution baseline is unreadable or incomplete") from error
    if result.get("status") != "UNCHANGED":
        report = _drift_report(project, coordinates["manifest"], result.get("changed") or [])
        changed = ", ".join(report["changed"]) or "входы baseline"
        raise HostStop(str(result.get("reason_code") or "BASELINE_DRIFT"),
                       f"файлы проекта изменились после scan: {changed}; {report['fix']}", report["fix"])


def _reconstruct_started_request(
    coordinates: Mapping[str, Any],
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
) -> tuple[Any, str]:
    """Rebuild the immutable closed request without inspecting generated bytes."""
    from tools.automation_validation import automation_sha256, required_symbol_pairs
    from tools.execution_adapters import ExecutionRequest, PYTEST, SYSTEM_MAVEN, request_digest
    from tools.pilot_state import derive_state, read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline

    root = Path(coordinates["run_root"])
    attempt = coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    module_root = Path(coordinates["module_root"]).resolve()
    try:
        baseline = read_execution_baseline(
            root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
        )
        effective = read_effective_canonical(root, attempt_id)
        durable_delta = read_attempt_receipt(
            root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK",
        )["record"]["delta"]
        if dict(document) != effective["document"] or automation_sha256(automation) != durable_delta["automation_digest"]:
            raise ValueError("recovery carriers do not bind durable inputs")
        pairs = sorted(required_symbol_pairs(dict(automation), dict(effective["document"])))
        artifacts = automation["artifacts"]
        files = {row["file_id"]: row for row in artifacts["generated_files"]}
        symbols = {(row["file_id"], row["symbol_id"]): row for row in artifacts["generated_symbols"]}
        selectors: list[str] = []
        for pair in pairs:
            file_row, symbol = files[pair[0]], symbols[pair]
            locator = symbol["locator"]
            if baseline["adapter_id"] == PYTEST:
                if locator["kind"] == "python_module_function":
                    node = locator["function_name"]
                elif locator["kind"] == "python_class_method":
                    node = "::".join([*locator["qualified_class_name"].split("."), locator["method_name"]])
                else:
                    raise ValueError("python recovery locator is invalid")
                selectors.append(f"{file_row['path']}::{node}")
            else:
                if locator["kind"] != "java_class_method":
                    raise ValueError("java recovery locator is invalid")
                selectors.append(f"{locator['class_fqn']}#{locator['method_name']}")
        adapter_id = baseline["adapter_id"]
        runtime_path = Path(
            baseline["interpreter_path"] if adapter_id == PYTEST else baseline["executable_path"] if adapter_id == SYSTEM_MAVEN else baseline["wrapper_path"]
        )
        from tools.project_inventory import module_runtime_path, system_maven_path
        executable = str(system_maven_path(str(runtime_path)) if adapter_id == SYSTEM_MAVEN else module_runtime_path(module_root, runtime_path.as_posix()))
        profile = str(baseline["build_profile"])
        typed = tuple(sorted((str(key), str(value)) for key, value in baseline["adapter_parameters"].items()))
        from tools.execution_adapters import command_for
        reports, argv = command_for(adapter_id, executable, profile, selectors)
        language = "python" if adapter_id == PYTEST else "java"
        request = ExecutionRequest(
            adapter_id, executable, tuple(argv), str(module_root), tuple(selectors), 600,
            reports, ("PROJECT_NATIVE_ENV",), profile, typed,
        )
        starts = [
            event for event in derive_state(root)["events"]
            if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_STARTED"
        ]
        if len(starts) != 1 or starts[0].get("artifact_digest") != request_digest(request):
            raise ValueError("reconstructed request does not bind execution start")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_RECOVERY", f"started execution request cannot be reconstructed: {error}") from error
    return request, language


def _lost_execution_report(
    coordinates: Mapping[str, Any],
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Project a lost post-start controller result as sparse authoritative UNKNOWN."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.execution_core import runner_report as _report

    request, language = _reconstruct_started_request(coordinates, document, automation)
    attempt_id = str(coordinates["attempt"]["attempt_id"])
    source = automation["artifacts"]["source"]
    return _report(
        "UNKNOWN", Path(coordinates["module_root"]), language, source,
        automation_sha256(automation), autotest_review_sha256(review),
        diagnostics=[{
            "path": "/execution", "code": "EXECUTION_RESULT_LOST",
            "message": "Execution started, but no authoritative framework or process result was durably recovered.",
        }],
        run_id=f"RUN-interrupted-{attempt_id}", authoritative=False, exit_code=2,
        duration_sec=0.0, request=request, run_root=Path(coordinates["run_root"]),
        attempt_id=attempt_id,
    )


def _publish_execution_receipt(coordinates: Mapping[str, Any], report: Mapping[str, Any]) -> Mapping[str, Any]:
    """Make the V5 result attempt-owned and prove its durable read-back."""
    from tools.pilot_state import (
        publish_attempt_receipt,
        recover_execution_receipt_events,
        record_execution_unknown,
        record_process_stopped,
    )

    root = Path(coordinates["run_root"])
    attempt_id = str(coordinates["attempt"]["attempt_id"])
    try:
        publish_attempt_receipt(root, attempt_id, "execution-receipt", {"payload": dict(report)})
        readback = recover_execution_receipt_events(root, attempt_id)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_RECEIPT", "execution completed but its durable receipt was not published/read back") from error
    if readback.get("record", {}).get("payload") != dict(report):
        raise HostStop("EXECUTION_RECEIPT", "execution receipt read-back does not equal the V5 report")
    if report.get("verdict") == "UNKNOWN":
        try:
            record_execution_unknown(root, attempt_id, str(readback["digest"]))
            if any(
                isinstance(row, Mapping)
                and row.get("process_scope_stopped") is True
                for row in report.get("process_evidence", [])
            ):
                record_process_stopped(root, attempt_id, str(readback["digest"]))
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise HostStop("EXECUTION_RECEIPT", "UNKNOWN execution receipt could not bind its durable event") from error
    return readback


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
            request=exact_request, run_root=root, attempt_id=attempt_id,
        )
        _publish_execution_receipt(coordinates, report)
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


def _execute(
    project: Path,
    coordinates: Mapping[str, Any],
    execution_root: Path,
    language: str,
    executor: str,
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
    authorization_receipt: Mapping[str, Any],
    host_isolation_receipt: Mapping[str, Any],
    generated_delta_receipt: Mapping[str, Any],
) -> int:
    from tools.exec_runner import run_tests_v5
    from tools.run_tests import (
        build_closed_execution_request,
        validate_execution_eligibility,
    )

    if executor != "local":
        return _reject_prestart_execution(
            coordinates, "EXECUTOR_UNSUPPORTED", "Pilot execution is project-native local only.",
            automation_artifact=automation, autotest_review=review,
        )

    try:
        if execution_root.resolve() != Path(coordinates["module_root"]).resolve():
            raise HostStop("EXECUTION_COORDINATES", "selected module differs from the attempt module")
        module = coordinates["module"]
        request, _compatibility = build_closed_execution_request(execution_root, language, module, document, automation)
    except HostStop as error:
        return _reject_prestart_execution(
            coordinates, error.code, str(error), automation_artifact=automation, autotest_review=review,
        )
    except (ValueError, TypeError) as error:
        return _reject_prestart_execution(
            coordinates, getattr(error, "code", "RUNNER_INPUT"), str(error),
            automation_artifact=automation, autotest_review=review,
        )
    try:
        _revalidate_execution_baseline(coordinates, request)
        eligibility = validate_execution_eligibility(
            request, authorization_receipt, automation, review,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]), attempt_id=str(coordinates["attempt"]["attempt_id"]),
        )
    except HostStop as error:
        return _reject_prestart_execution(
            coordinates, error.code, str(error), request=request,
            automation_artifact=automation, autotest_review=review,
        )
    except (ValueError, TypeError, OSError) as error:
        return _reject_prestart_execution(
            coordinates, "BASELINE_INCOMPLETE", str(error), request=request,
            automation_artifact=automation, autotest_review=review,
        )
    if eligibility.get("ready") is not True:
        return _reject_prestart_execution(
            coordinates, str(eligibility.get("reason_code", "RUNNER_INPUT")), "execution admission is not ready",
            request=request, automation_artifact=automation, autotest_review=review,
        )

    execution_claimed = False

    def on_execution_start(request_value: Any) -> None:
        nonlocal execution_claimed
        from tools.execution_adapters import request_digest
        from tools.pilot_state import claim_execution_start

        # This callback is the last controller hook before subprocess invocation:
        # re-check both the frozen baseline and the durable generated delta before
        # claiming the one execution start.  Baseline inventory intentionally
        # excludes the pipeline-owned delta, so it cannot prove those bytes.
        _revalidate_execution_baseline(coordinates, request_value)
        final_eligibility = validate_execution_eligibility(
            request_value, authorization_receipt, automation, review,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]), attempt_id=str(coordinates["attempt"]["attempt_id"]),
        )
        if final_eligibility.get("ready") is not True:
            raise HostStop(
                str(final_eligibility.get("reason_code", "RUNNER_INPUT")),
                "durable execution admission changed before the execution-start gate",
            )
        claim_execution_start(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]), request_digest(request_value))
        execution_claimed = True

    def post_execution_check() -> str | None:
        try:
            _revalidate_execution_baseline(coordinates, request)
        except HostStop as error:
            return error.code
        return None

    try:
        report = run_tests_v5(
            request, authorization_receipt, document, automation, review,
            host_isolation_receipt=host_isolation_receipt,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]),
            attempt_id=str(coordinates["attempt"]["attempt_id"]),
            on_execution_start=on_execution_start,
            post_execution_check=post_execution_check,
        )
    except HostStop as error:
        if not execution_claimed:
            return _reject_prestart_execution(
                coordinates, error.code, str(error), request=request,
                automation_artifact=automation, autotest_review=review,
            )
        raise
    try:
        _publish_execution_receipt(coordinates, report)
    except HostStop as error:
        _print({"status": "error", "reason": error.code, "message": str(error), "attempt_id": coordinates["attempt"]["attempt_id"]})
        return 2
    try:
        from tools.finalize_attempt import finalize_durable_execution_attempt

        closed = finalize_durable_execution_attempt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        _print({
            "status": "error", "reason": "FINALIZATION", "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    result = closed["result"]
    _print({
        "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
        "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": result.get("verification"),
        "accepted": result.get("accepted"), "executor": "local", "finalization": "complete",
        **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        **_automation_split(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
    })
    return int(closed["exit_code"])


_NOT_PREPARED_FIX = ("exec запускает только принятую автоматизацию: заверши статическое ревью автотестов "
                     "(autotest-reviewer, вердикт ПРИНЯТО), затем снова exec")


# FaT-H2b 2.9: after a review that did not accept the automation — never push for another review past the limit
_REFUSED_FIX = ("статическое ревью не приняло автоматизацию, ревизий больше нет: закройте попытку командой finish — "
                "она напечатает next_run, команду следующего захода")


_REVISION_FIX = "ревью вернуло автоматизацию на правку: следующая ревизия tc-to-autotest:r2, её ревью autotest-reviewer:r2, затем exec"


# FaT-RL: the review accepted the automation, but exec cannot write it into the project — no later step changes that
_UNMATERIALIZABLE_FIX = ("ревью приняло автоматизацию, но exec не может записать её в проект (причина в message): закройте попытку "
                         "командой finish — она закроет её отрицательно (AUTOMATION_NOT_MATERIALIZABLE) и напечатает next_run")


def _not_prepared_fix(root: Path, attempt_id: str) -> str:
    from tools.finalize_attempt import automation_next

    try:
        outcome, detail = automation_next(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return _NOT_PREPARED_FIX
    if outcome == "PENDING":
        return _REVISION_FIX if detail == "tc-to-autotest:r2" else (
            f"автоматизация не закончена: следующий шаг — {detail}, затем ревью автотестов (ПРИНЯТО) и снова exec")
    return {"AUTOMATION_REJECTED": _REFUSED_FIX, "AUTOMATION_BLOCKED": _REFUSED_FIX,
            "AUTOMATION_NOT_MATERIALIZABLE": _UNMATERIALIZABLE_FIX}.get(outcome, _NOT_PREPARED_FIX)


def _prepare_execution(coordinates: Mapping[str, Any]) -> list[str]:
    """FaT-E: before the first run, materialize the accepted reviewed automation of the attempt, if not done yet.

    Inside the exec step, not a driver: the same ``materialize_delta`` the agent would call, from the journal's own
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


# FaT-AB D3: the accepted automation passed the precheck, then its preparation failed
_PREPARE_IO_FIX = ("подготовка принятой автоматизации прервалась на вводе-выводе (причина в message): снова вызови exec — "
                   "ответы и журнал те же, повтор безопасен")


_PREPARE_FINAL_FIX = ("подготовка принятой автоматизации отказала после записанных входов исполнения, и повтор exec ответит так же: "
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


def cmd_exec(args: Any) -> int:
    project = Path(args.project).resolve()
    try:
        coordinates = _validated_execution_coordinates(project, args.run)
    except HostStop as error:
        _print({"status": "error", "reason": error.code, "message": str(error)})
        return 2
    try:
        from tools.pilot_state import exit_code, read_terminal_result

        terminal = read_terminal_result(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]))
    except KeyError:
        terminal = None
    if terminal is not None:
        _print({
            "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
            "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": terminal.get("verification"),
            "accepted": terminal.get("accepted"), "executor": "local", "finalization": "complete",
            **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_automation_split(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        })
        return exit_code(terminal)
    execution_receipt_path = (
        Path(coordinates["run_root"]) / "execution-receipts" / f"{coordinates['attempt']['attempt_id']}.json"
    )
    from tools.pilot_state import derive_state, read_attempt_receipt, read_effective_canonical, read_execution_inputs

    started = [
        event for event in derive_state(Path(coordinates["run_root"]))["events"]
        if event.get("attempt_id") == coordinates["attempt"]["attempt_id"]
        and event.get("event_type") == "EXECUTION_STARTED"
    ]
    if not execution_receipt_path.exists() and not started:
        # FaT-2R: the package never starts the application; before any write a module with a stand checks it answers.
        # A stop here leaves the attempt open: the person brings the stand up and runs exec again (no files, no quarantine).
        stand = _service_stop(project, str(coordinates["attempt"]["module"]))
        if stand is not None:
            # FaT-O12 O2: the stand that cannot be brought up closes the attempt negatively — the agent's call, checked by finish
            closing = (f'"{sys.executable}" -m tools.run_pipeline finish --project "{project}" --run {Path(coordinates["run_root"]).name} '
                       "--abandon STAND_UNAVAILABLE")
            _print({"status": "error", **stand, "fix": f"{stand['fix']}; стенд не поднять — {closing}",
                    "attempt_id": coordinates["attempt"]["attempt_id"]})
            return 2
    if execution_receipt_path.exists() or started:
        try:
            from tools.finalize_attempt import finalize_durable_execution_attempt
            from tools.pilot_state import (
                publish_resume_validation,
                read_closure_artifact_if_present,
                read_resume_validation_if_present,
                recover_execution_receipt_events,
            )

            effective = read_effective_canonical(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
            execution_inputs = read_execution_inputs(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
            document = effective["document"]
            automation = execution_inputs["automation_artifact"]
            review = execution_inputs["autotest_review"]
            if execution_receipt_path.exists():
                recovered_execution = recover_execution_receipt_events(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
            else:
                _publish_execution_receipt(
                    coordinates, _lost_execution_report(coordinates, document, automation, review),
                )
                recovered_execution = recover_execution_receipt_events(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
            if recovered_execution["record"]["payload"]["verdict"] != "NOT_RUNNABLE":
                # Atomic installation freezes the verifier inputs.  Recovery may
                # finish its missing read-back binding, but terminal publication
                # still remains gated on that completed binding.
                finalization_receipt = read_closure_artifact_if_present(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                    "finalization_receipt",
                )
                resume_validation = read_resume_validation_if_present(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
                if finalization_receipt is None and resume_validation is None:
                    request, _language = _reconstruct_started_request(coordinates, document, automation)
                    try:
                        _revalidate_execution_baseline(coordinates, request)
                    except HostStop as drift:
                        publish_resume_validation(
                            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                            drift.code,
                        )
            closed = finalize_durable_execution_attempt(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
        except (HostStop, KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            _print({
                "status": "error",
                "reason": error.code if isinstance(error, HostStop) else "FINALIZATION",
                "message": str(error),
                "attempt_id": coordinates["attempt"]["attempt_id"],
            })
            return 2
        result = closed["result"]
        _print({
            "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
            "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": result.get("verification"),
            "accepted": result.get("accepted"), "executor": "local", "finalization": "complete",
            **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_automation_split(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        })
        return int(closed["exit_code"])
    try:
        _prepare_execution(coordinates)
    except HostStop as stop:
        _print({"status": "error", "reason": stop.code, "message": str(stop), **({"fix": stop.fix} if stop.fix else {}),
                "attempt_id": coordinates["attempt"]["attempt_id"]})
        return 2
    try:
        from tools.canonical_document import load_canonical_document
        from tools.run_tests import load_automation_artifact, load_autotest_review_artifact
        from tools.schema_validation import load_json_strict

        # FaT-4: the six carriers come from the attempt's durable evidence; a path given is a check of the same bytes.
        loaders = {
            "canonical_document": load_canonical_document, "automation_artifact": load_automation_artifact,
            "autotest_review": load_autotest_review_artifact, "authorization_receipt": load_json_strict,
            "host_isolation_receipt": load_json_strict, "generated_delta_receipt": load_json_strict,
        }
        supplied = {name: loader(Path(getattr(args, name))) for name, loader in loaders.items() if getattr(args, name, None)}
        effective = read_effective_canonical(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
        execution_inputs = read_execution_inputs(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
        durable_delta = read_attempt_receipt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            "generated-delta", "ARTIFACT_READ_BACK",
        )["record"]
        review_revision = execution_inputs["autotest_review"]["artifacts"]["autotest_review"]["automation_revision"]
        durable_isolation = read_attempt_receipt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            f"automation-review-boundary-r{review_revision}", "ARTIFACT_READ_BACK",
        )["record"]
        durable = {
            "canonical_document": effective["document"], "automation_artifact": execution_inputs["automation_artifact"],
            "autotest_review": execution_inputs["autotest_review"], "authorization_receipt": coordinates["authorization"],
            "host_isolation_receipt": durable_isolation, "generated_delta_receipt": durable_delta,
        }
        differing = [name for name, value in supplied.items() if value != durable[name]]
        if differing:
            raise ValueError("execution carrier differs from durable attempt evidence: " + ", ".join("--" + name.replace("_", "-") for name in differing))
        document = effective["document"]
        automation = execution_inputs["automation_artifact"]
        review = execution_inputs["autotest_review"]
        authorization = coordinates["authorization"]
        isolation = durable_isolation
        delta = durable_delta
    except (HostStop, KeyError, ValueError, TypeError, OSError) as error:
        code = error.code if isinstance(error, HostStop) else getattr(error, "code", "RUNNER_INPUT")
        _print({
            "status": "error", "reason": code, "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    try:
        execution_root, language, module = _resolve_host_context(
            project, getattr(args, "module", None), getattr(args, "language", None)
        )
        if execution_root.resolve() != Path(coordinates["module_root"]).resolve():
            raise HostStop("EXECUTION_COORDINATES", "selected module differs from the attempt module")
        coordinates = {**coordinates, "module": module}
    except (HostStop, ValueError, TypeError, OSError) as error:
        code = error.code if isinstance(error, HostStop) else getattr(error, "code", "RUNNER_INPUT")
        return _reject_prestart_execution(
            coordinates, code, str(error), automation_artifact=automation, autotest_review=review,
        )
    return _execute(
        project, coordinates, execution_root, language, getattr(args, "executor", "local"), document, automation, review,
        authorization, isolation, delta,
    )


def _rerun_request_current(request: Any) -> None:
    """FaT-H2a: a Gradle request recorded before --no-build-cache would replay the build cache, not rerun the tests."""
    from tools.execution_adapters import GRADLE

    if request.adapter_id == GRADLE and "--no-build-cache" not in request.argv:
        raise HostStop(
            "RERUN_REQUEST_OUTDATED",
            "команда Gradle изменилась в пакете: теперь cleanTest test --no-build-cache, а этот прогон записан старой "
            "командой — её повтор может взять результат тестов из кэша сборки, а не запустить их",
            "перепроверка этого прогона невозможна; начните новый прогон тем же scan, его тесты запустятся новой командой",
        )


def rerun_retained_tests(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Rerun one accepted attempt's retained tests through its recorded native request."""
    from tools.execution_adapters import invoke_request, request_digest
    from tools.pilot_state import (
        append_event,
        derive_state,
        publish_retained_rerun_observation,
        publish_run_artifact_bytes,
        read_effective_canonical,
        read_execution_inputs,
        read_retained_native_rerun,
        read_scenario_observation,
    )
    from tools.execution_core import (
        append_journal_event as _append_event,
        publish_artifact_value as _publish,
        read_artifact_value as _read_artifact,
        retained_native_rerun_target as _retained_native_rerun_target,
        retained_rerun_context as _retained_rerun_context,
        scenario_observation_target as _scenario_observation_target,
    )
    from tools.exec_runner import invoke_closed_request as _invoke_closed_request
    from tools.run_tests import DURABLE_NATIVE_REPORT_NORMALIZATION, validate_artifact_runner_compatibility
    from tools.execution_core import (
        normalize_durable_junit_report as _normalize_durable_junit_report,
        closed_outcome as _outcome,
        report_inventory as _report_inventory,
        report_snapshots as _report_snapshots,
        records_from_snapshots as _records_from_snapshots,
        request_report_path as _request_report_path,
    )

    context = _retained_rerun_context(Path(run_root), attempt_id)
    project, root = context["project"], context["root"]
    state = derive_state(root)
    rerun_events = [
        row for row in state["events"]
        if row.get("event_type") == "RETAINED_NATIVE_RERUN"
        and row.get("attempt_id") == attempt_id
    ]
    if len(rerun_events) > 1:
        raise ValueError("retained native rerun lifecycle is ambiguous")
    if rerun_events:
        receipt = read_retained_native_rerun(
            root, attempt_id, str(rerun_events[0].get("artifact_digest", "")),
        )
        observation_target = _scenario_observation_target(
            root, attempt_id, "retained-native-rerun",
        )
        if observation_target.exists():
            observation_record = _read_artifact(
                project, root, observation_target, "scenario observation",
            )
            observation = read_scenario_observation(
                root, attempt_id, "retained-native-rerun",
                str(observation_record["digest"]),
            )
        else:
            observation = publish_retained_rerun_observation(root, attempt_id)
        return {
            "rerun_receipt": receipt,
            "scenario_observation_receipts": {"retained-native-rerun": observation},
            "idempotent": True,
        }
    if _retained_native_rerun_target(root, attempt_id).exists():
        raise ValueError("unbound retained native rerun receipt exists")
    observation_target = _scenario_observation_target(
        root, attempt_id, "retained-native-rerun",
    )
    if observation_target.exists():
        raise ValueError("unbound retained native rerun observation exists")

    request = context["request"]
    if request_digest(request) != context["execution_receipt"]["execution_request_digest"]:
        raise ValueError("retained native rerun request binding is invalid")
    _rerun_request_current(request)
    executable = Path(request.executable)
    if not executable.is_file() or (os.name != "nt" and not os.access(executable, os.X_OK)):
        raise ValueError("retained native rerun executable is unavailable")
    if os.name == "nt" and executable.suffix.casefold() == ".exe":
        try:
            with executable.open("rb") as stream:
                executable_header = stream.read(2)
        except OSError as error:
            raise ValueError("retained native rerun executable is unavailable") from error
        if executable_header != b"MZ":
            raise ValueError("retained native rerun executable is invalid")
    report_path = _request_report_path(request)
    if report_path is None:
        raise ValueError("retained native rerun report path is invalid")
    execution_payload = context["execution_receipt"]["payload"]
    language = execution_payload["target"]["language"]
    effective = read_effective_canonical(root, attempt_id)
    execution_inputs = read_execution_inputs(root, attempt_id)
    compatibility = validate_artifact_runner_compatibility(
        Path(request.cwd).resolve(), language,
        effective["document"], execution_inputs["automation_artifact"],
    )
    if compatibility.status != "READY":
        raise ValueError("retained native rerun reviewed targets are invalid")
    before_inventory = _report_inventory(report_path)
    outcome = _outcome(invoke_request(request, _invoke_closed_request))
    after_inventory = _report_inventory(report_path)
    fresh_paths = sorted(
        (path for path, value in after_inventory.items() if before_inventory.get(path) != value),
        key=str,
    )
    snapshots = _report_snapshots(fresh_paths)
    after_context = _retained_rerun_context(root, attempt_id)
    if after_context["snapshot"] != context["snapshot"]:
        raise ValueError("retained native rerun changed retained test bytes")
    if outcome.kind != "EXIT" or outcome.exit_code != 0 or not fresh_paths:
        raise ValueError("retained native rerun did not produce a passing fresh report")
    if set(snapshots) != set(fresh_paths):
        raise ValueError("retained native rerun report read-back is incomplete")
    rerun_evidence, match_errors = _records_from_snapshots(
        snapshots, compatibility, str(execution_payload["run_id"]),
        execution_payload["source"], java=language == "java",
    )
    expected_pairs = sorted(
        (row["file_id"], row["symbol_id"])
        for row in execution_payload["execution_evidence"]
    )
    observed_pairs = sorted(
        (row["file_id"], row["symbol_id"])
        for row in rerun_evidence
    )
    if (
        match_errors or observed_pairs != expected_pairs
        or any(row["status"] != "PASSED" for row in rerun_evidence)
    ):
        raise ValueError("retained native rerun report does not match reviewed targets")

    def fingerprint(value: tuple[int, int, str] | None) -> Mapping[str, Any] | None:
        if value is None:
            return None
        return {"mtime_ns": value[0], "size": value[1], "digest": "sha256:" + value[2]}

    reports: list[dict[str, Any]] = []
    used_names: dict[str, int] = {}
    cwd = Path(request.cwd).resolve()
    for path in fresh_paths:
        raw = snapshots[path]
        after_fingerprint = fingerprint(after_inventory[path])
        source_digest = _digest_bytes(raw)
        if (
            after_fingerprint is None
            or after_fingerprint["size"] != len(raw)
            or after_fingerprint["digest"] != source_digest
        ):
            raise ValueError("retained native rerun report is not a passing fresh report")
        try:
            source_path = path.relative_to(cwd).as_posix()
        except ValueError as error:
            raise ValueError("retained native rerun report escaped its module") from error
        name = path.name or "report.xml"
        count = used_names.get(name, 0)
        used_names[name] = count + 1
        if count:
            name = f"{Path(name).stem}-{count}{Path(name).suffix}"
        durable = _normalize_durable_junit_report(raw)
        published = publish_run_artifact_bytes(
            root, attempt_id, f"retained-rerun/reports/{name}", durable,
        )
        reports.append({
            "source_path": source_path,
            "source_digest": source_digest,
            "before_fingerprint": fingerprint(before_inventory.get(path)),
            "after_fingerprint": after_fingerprint,
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": published["path"],
            "digest": published["digest"],
        })
    value = {
        "schema_version": "1.0.0", "kind": "retained-native-rerun",
        "run_id": context["attempt"]["run_id"], "attempt_id": attempt_id,
        "policy_profile": context["attempt"]["policy_profile"],
        "terminal_result_digest": context["terminal"]["digest"],
        "execution_receipt_digest": context["execution_receipt"]["digest"],
        "execution_request_digest": context["execution_receipt"]["execution_request_digest"],
        "disposition_receipt_digest": context["disposition_receipt"]["digest"],
        "before": context["snapshot"], "after": after_context["snapshot"],
        "outcome": {"kind": outcome.kind, "exit_code": outcome.exit_code},
        "reports": reports,
    }
    target = _retained_native_rerun_target(root, attempt_id)
    record, created, identity = _publish(
        project, root, target, value, "retained native rerun", return_created=True,
    )
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=record["digest"])
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", artifact_digest=record["digest"])
    _append_event(
        project, root, "RETAINED_NATIVE_RERUN", actor="controller",
        attempt_id=attempt_id, batch_id=None, artifact_digest=record["digest"],
    )
    receipt = dict(read_retained_native_rerun(root, attempt_id, str(record["digest"])))
    receipt.update({"created": created, "installed_identity": identity})
    observation = publish_retained_rerun_observation(root, attempt_id)
    return {
        "rerun_receipt": receipt,
        "scenario_observation_receipts": {"retained-native-rerun": observation},
        "idempotent": False,
    }


def cmd_rerun_retained(args: Any) -> int:
    project = Path(args.project).resolve()
    root, run, state = _read_run_coordinate(project, args.run)
    from tools.pilot_state import read_terminal_result

    candidates = []
    for attempt in state["attempts"]:
        if attempt.get("state") != "TERMINAL":
            continue
        terminal = read_terminal_result(root, str(attempt["attempt_id"]))
        if terminal.get("accepted") is True and terminal.get("verification") == "PASS":
            candidates.append(attempt)
    if len(candidates) != 1:
        raise HostStop(
            "RERUN_COORDINATES",
            "run must contain exactly one accepted passing terminal attempt",
        )
    attempt_id = str(candidates[0]["attempt_id"])
    result = rerun_retained_tests(root, attempt_id)
    observation = result["scenario_observation_receipts"]["retained-native-rerun"]
    _print({
        "status": "ok", "stage": "rerun-retained",
        "run_id": run["manifest"]["run_id"], "attempt_id": attempt_id,
        "rerun_receipt_digest": result["rerun_receipt"]["digest"],
        "scenario_observation_digest": observation["digest"],
        "idempotent": result["idempotent"],
    })
    return 0


def add_parsers(sub: Any, shared: Any) -> None:
    """The parsers of ``exec`` and ``rerun-retained`` — added by ``run_pipeline.build_parser`` when this module is in the package."""
    exec_parser = sub.add_parser("exec", parents=[shared])
    exec_parser.add_argument("--run", required=True)
    exec_parser.add_argument("--language", choices=("python", "java"))
    exec_parser.add_argument("--executor", choices=("local",), default="local")
    exec_parser.add_argument("--canonical-document")
    exec_parser.add_argument("--automation-artifact")
    exec_parser.add_argument("--autotest-review")
    exec_parser.add_argument("--authorization-receipt")
    exec_parser.add_argument("--host-isolation-receipt")
    exec_parser.add_argument("--generated-delta-receipt")
    rerun_parser = sub.add_parser("rerun-retained", parents=[shared])
    rerun_parser.add_argument("--run", required=True, help="Exact durable run ID")


COMMANDS = {
    "exec": cmd_exec,
    "rerun-retained": cmd_rerun_retained,
}
