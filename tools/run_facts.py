"""The facts of a run: what its journal and receipts say, read only — the one computation that status, finish, the report and the
execution commands show (v0.8, C1).  Nothing here prints, runs a step or names the next command: that is ``run_pipeline``
(``_durable_status``) and ``tools.stages``."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import execution_gate
from tools.fixes import human_for
from tools.stages import (AUTOMATION, AUTOMATION_REVIEW, CLOSURE, DRIFT, GENERATOR, HUMAN_RUN, MARKER, MATERIALIZE, REVIEW, REVIEW_ANSWER, SCAN,
                          answer_of)


def _exclusion_summary(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the scan left out, for a person; derived from the attempt's frozen inventory, never stored."""
    from tools.pilot_state import frozen_baseline
    from tools.project_inventory import read_inventory_receipt
    from tools.scan_rules import exclusion_summary

    root = Path(run_root)
    try:
        baseline = frozen_baseline(root, attempt)
        inventory = read_inventory_receipt(root.parent.parent, root, root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    except (KeyError, OSError, ValueError):
        return None
    return exclusion_summary(inventory["exclusions"], str(attempt.get("module") or "."))


def _batches_all_answered(root: Path, attempt_id: str, responses: Sequence[Mapping[str, Any]]) -> bool:
    """Whether every batch of the published plan has its answer in the journal (only reads; an unreadable plan is «not yet»)."""
    from tools import pilot_state as pilot

    try:
        batches = pilot.read_attempt_receipt(root, attempt_id, "batch-plan", "ARTIFACT_READ_BACK")["record"]["plan"]["batches"]
        return bool(batches) and {f"tc-generator:{row['batch_id']}" for row in batches} <= {row.get("stage_instance_id") for row in responses}
    except (OSError, KeyError, TypeError, ValueError):
        return False


def _quarantine_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """What the FAIL closure did to the generated tests, read from the attempt's disposition receipt."""
    from tools import pilot_state as pilot

    try:
        rows = pilot.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["files"]
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    quarantine = [
        {"file": row["path"], "method": item["method"], "ref": item["ref"], "message": item["message"],
         **{key: item[key] for key in ("kind", "failed_rows", "disabled_passed_rows") if key in item}}  # the kind of the failure and a group method's rows
        for row in rows if row.get("disposition") == "QUARANTINED" for item in row["quarantine"]
    ]
    unresolved = [row["path"] for row in rows if row.get("reason_code") == "QUARANTINE_UNRESOLVED"]
    if not quarantine and not unresolved:
        return {}
    return {"quarantine": quarantine, **({"quarantine_unresolved": (
        "QUARANTINE_UNRESOLVED: упавшие методы не удалось однозначно сопоставить с отчётом ingest, "
        f"поэтому сгенерированные файлы убраны целиком, включая прошедшие тесты: {', '.join(unresolved)}"
    )} if unresolved else {})}


def _review_notes_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """How many notes the accepted case/autotest review left and where its answer is; nothing when there are none."""
    from tools.run_report import review_notes

    summary: dict[str, Any] = {}
    for field, review in (("review_notes", "cases"), ("autotest_review_notes", "autotests")):
        try:
            notes = review_notes(run_root, attempt_id, review)
        except (KeyError, OSError, TypeError, ValueError):  # a hint never fails the step
            continue
        if notes["count"]:
            summary[field] = {"count": notes["count"], "path": notes["path"]}
    return summary


_KEPT_DISPOSITIONS = frozenset({"RETAINED", "QUARANTINED", "PRESERVED_EXECUTION_UNKNOWN", "PRESERVED_CONTENT_CONFLICT",
                                 "PRESERVED_CLEANUP_CONFLICT"})


def _style_notes(project: Path, run_root: Path, attempt_id: str) -> dict[str, str]:
    """The project's style checks the selected-tests run skipped and how to format the files left in the project."""
    if not execution_gate.mode_b_available():
        return {}  # a delivery of cases has no build and no style checks to report
    adapters = execution_gate.load_mode_b("execution_adapters")
    from tools import pilot_state as pilot
    from tools.project_inventory import read_execution_baseline

    try:
        attempt = next(row for row in pilot.derive_state(run_root)["attempts"] if row.get("attempt_id") == attempt_id)
        baseline = read_execution_baseline(Path(project), Path(run_root), Path(run_root) / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        project_root = Path(project).resolve()
        module_root = project_root if attempt["module"] == "." else project_root.joinpath(*str(attempt["module"]).split("/"))
        adapter_id = str(baseline["adapter_id"])
        runtime = str(baseline[{adapters.PYTEST: "interpreter_path", adapters.SYSTEM_MAVEN: "executable_path"}.get(adapter_id, "wrapper_path")])
        executable = runtime if adapter_id == adapters.SYSTEM_MAVEN else str(module_root / runtime)
        try:
            rows = pilot.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["files"]
        except (KeyError, OSError, TypeError, ValueError):
            rows = []
        kept = tuple(str(row["path"]) for row in rows if row.get("disposition") in _KEPT_DISPOSITIONS)
        return adapters.style_notes(adapter_id, executable, adapters.project_style_checks(project_root, module_root), kept)
    except (KeyError, OSError, StopIteration, TypeError, ValueError):  # a hint never fails the step
        return {}


def _report_mismatch(run_root: Path, attempt_id: str) -> dict[str, str]:
    """Why an UNKNOWN run could not match its JUnit report, and the fix — never the report text.

    A testcase whose method is selected but whose classname is none of that method's selected classes is what a class
    display name does (Surefire writes it as the classname); the hint names the selected classes only."""
    if not execution_gate.mode_b_available():
        return {}  # a delivery of cases has no test report to match
    from tools import pilot_state as pilot

    parse_junit_bytes, selected_classes_renamed = execution_gate.from_mode_b("run_tests", "parse_junit_bytes", "selected_classes_renamed")

    try:
        payload = pilot.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        if payload.get("verdict") != "UNKNOWN" or not any(row.get("kind") == "JUNIT_INVALID" for row in payload.get("process_evidence", [])):
            return {}
        execution = payload["execution"]
        classes: set[str] = set()
        for row in execution["artifact_evidence"]:
            relative = Path(str(row.get("path", "")))
            if row.get("kind") != "native_report" or relative.is_absolute() or relative.parts[:2] != ("artifacts", attempt_id) or ".." in relative.parts:
                continue
            # audit U4: the reading of run_tests, the one of the verdict
            classes |= selected_classes_renamed(parse_junit_bytes((Path(run_root) / relative).read_bytes()), execution["selectors"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError):  # a hint never fails the step
        return {}
    if not classes:
        return {}
    return {
        "report_mismatch": (f"отчёт JUnit не сопоставлен с тестами: методы класса {', '.join(sorted(classes))} записаны в отчёт "
                            "под другим classname — так делает @DisplayName на классе теста"),
        "report_fix": "убери @DisplayName с класса и методов теста и начни новый прогон тем же scan",
    }


def _last_refusal(root: Path, events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The last line of ``refusals.jsonl`` a ``COMMAND_REFUSED`` event of the journal confirms by the sha256 of the line (as ``run_report.refusals``
    checks them: a line no event confirms is not believed); None without one."""
    try:
        lines = {"sha256:" + hashlib.sha256(line).hexdigest(): line for line in (root / "refusals.jsonl").read_bytes().split(b"\n") if line.strip()}
    except OSError:
        return None
    for event in reversed(events):
        line = lines.get(str(event.get("artifact_digest"))) if event.get("event_type") == "COMMAND_REFUSED" else None
        try:
            row = json.loads(line) if line is not None else None
        except ValueError:
            continue
        if isinstance(row, Mapping) and isinstance(row.get("code"), str):
            return row
    return None


def human_summary(acceptance: Mapping[str, Any], csv: str | None, present: bool) -> str:
    """The outcome of the review of a set of cases in one line for a person (``human_summary`` of finish and status)."""
    total, accepted = acceptance["generated"], acceptance["accepted"]
    if acceptance["outcome"] == "REWORK":
        why = (f"ревью отклонило {acceptance['removed']} из {total} (больше допустимой доли)" if acceptance.get("reason") == "OVER_SHARE"
               else "замечание ревьюера относится ко всему набору")
        return f"Кейсы требуют доработки: {why}, файл для Zephyr не создан."
    whole = acceptance["outcome"] == "ACCEPTED_WHOLE"
    head = f"Кейсы приняты ({accepted} из {total})" if whole else f"Кейсы приняты частично ({accepted} из {total}): {total - accepted} не вошли в файл (отклонены ревью или сняты вместе со своей группой)"
    if not present:
        return f"{head}, но файла для Zephyr в прогоне нет" + (f": {csv}." if csv else ".")
    rejected = "" if whole else f", отклонённые — {str(csv).removesuffix('.zephyr-scale.csv')}.REJECTED-CASES.html"
    return f"{head}{',' if whole else ';'} файл для Zephyr готов: {csv}{rejected}."


def _human_summary(project: Path, root: Path, attempt_id: str, manifest: Mapping[str, Any]) -> dict[str, str]:
    """``human_summary`` of a run of cases only, from the same outcome the report draws; nothing before the verdict of the review."""
    if manifest.get("policy_profile") != "cases-only-v1":
        return {}
    from tools.openspec_scope import summary_tail
    from tools.orchestrate_test_case_revision import acceptance_summary
    from tools.pilot_state import derive_state, read_effective_canonical_if_present, read_model_stage_artifact
    from tools.review_canaries import recorded_answer

    try:
        acceptance = acceptance_summary(root, attempt_id, recorded_answer(root, attempt_id))
        effective = read_effective_canonical_if_present(root, attempt_id) if acceptance else None
    except (KeyError, OSError, TypeError, ValueError):  # a line for a person never fails the command
        return {}
    try:  # the tail of a change only: a marker that does not read leaves the line as it was
        marker = next((event for event in derive_state(root)["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                       and event.get("stage_instance_id") == "context-marker:baseline"), None) if acceptance else None
        warnings = read_model_stage_artifact(root, attempt_id, "context-marker:baseline", str(marker["artifact_digest"]))["artifact"].get("warnings") or [] if marker else []
    except (KeyError, OSError, TypeError, ValueError):
        warnings = []
    if acceptance is None:
        return {}
    document = (effective or {}).get("document") or {}
    csv = root / "published" / f"{document['document_id']}.r{document['revision']}.zephyr-scale.csv" if document else None
    try:
        shown = None if csv is None else csv.relative_to(Path(project).resolve()).as_posix()
    except ValueError:
        shown = str(csv)
    return {"human_summary": summary_tail(human_summary(acceptance, shown, csv is not None and csv.is_file()), warnings)}


def _durable_status_facts(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    from tools import pilot_state as pilot
    from tools.project_inventory import read_execution_baseline
    from tools.revision_selection import REWORK_REVIEW_STAGE, REWORK_STAGE

    root = Path(run_root).resolve()
    run = pilot.read_run(root)
    state = pilot.derive_state(root)
    attempt = state["attempts"][-1] if state["attempts"] else None
    result = {
        "status": status,
        "stage": stage,
        "project": str(Path(project).resolve()),
        "run_id": run["manifest"]["run_id"],
        "run_root": str(root),
        "attempt_id": attempt.get("attempt_id") if attempt else None,
        "attempt_state": attempt.get("state") if attempt else None,
        "event_count": len(state["events"]),
        "last_confirmed_stage": "run-created",
        "next_step": SCAN,
        "stop_reason": None,
        "evidence_path": str(root / "run-manifest.json"),
        "policy_profile": run["manifest"]["policy_profile"],
        **{key: run["manifest"][key] for key in ("requested_policy_profile", "execution_skipped") if key in run["manifest"]},
        **({"self_review_accepted": True} if pilot.self_review_accepted(root) else {}),  # at scan or at the step of the review
        **({"disposition_policy": "cleanup"} if run["authorization"].get("disposition_policy") == "cleanup" else {}),
        **(_quarantine_summary(root, attempt["attempt_id"]) if attempt else {}),
        "warnings": [], "warning_evidence_path": None, "evidence_errors": [],
        **extra,
    }
    if attempt is None:
        last = _last_refusal(root, state["events"])
        result["stop_detail"] = ("No attempt yet; an early scan failure may have no persisted cause." if last is None else
                                 f"Попытки ещё нет; последний отказ этого прогона: {last['code']} ({last.get('command')}, {last.get('at')}).")
        if last is not None and (said := human_for(last["code"])):
            result["stop_human"] = said
        return result
    summary = _exclusion_summary(root, attempt)
    if summary is not None:
        result["exclusion_summary"] = summary
    attempt_id = attempt["attempt_id"]
    events = [row for row in state["events"] if row.get("attempt_id") == attempt_id]
    confirmed: list[tuple[int, dict[str, Any]]] = []

    def record(digest: str, label: str, next_artifact: tuple[str, str | None], path: str, **facts: Any) -> None:
        sequence = max((row["seq"] for row in state["events"] if row.get("artifact_digest") == digest and row.get("attempt_id") in (None, attempt_id)), default=0)
        confirmed.append((sequence, {"last_confirmed_stage": label, "next_step": next_artifact, "evidence_path": str(root / path), **facts}))

    def unreadable(path: str) -> None:
        result["evidence_errors"].append({"code": "EVIDENCE_UNVERIFIED", "path": str(root / path)})

    # Requirement gaps survive later empty warning arrays. Current-source drift must
    # not erase an independently valid historical terminal result.
    responses = [row for row in events if row["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    marker = next((row for row in responses if row.get("stage_instance_id") == "context-marker:baseline"), None)
    model_outputs = {}
    if marker:
        marker_path = f"model-stage-artifacts/{attempt_id}/{marker['artifact_digest'][7:]}.json"
        try:
            publication = pilot.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", marker["artifact_digest"])
            model_outputs[marker["artifact_digest"]] = publication
            result["warnings"] = list(publication["artifact"]["warnings"])
            result["warning_evidence_path"] = str(root / publication["path"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(marker_path)
    if attempt["state"] == "TERMINAL":
        terminal = pilot.read_terminal_result(root, attempt_id)
        result.update(last_confirmed_stage="terminal", next_step=None,
                      evidence_path=str(root / "terminal-results" / f"{attempt_id}.json"),
                      stop_reason=terminal.get("reason_code"))
        result.update({key: terminal[key] for key in ("completion", "verification", "coverage", "accepted")})
        return result

    baseline_path = f"baselines/{attempt['baseline_digest'][7:]}.json"
    try:
        baseline = read_execution_baseline(Path(project), root, root / baseline_path)
        if baseline["digest"] != attempt["baseline_digest"]:
            raise ValueError("baseline digest mismatch")
        record(baseline["digest"], "baseline", SCAN, baseline_path)
    except (OSError, KeyError, TypeError, ValueError):
        unreadable(baseline_path)
    contexts = [row for row in events if row["event_type"] == "CONTEXT_SELECTED"]
    if contexts:
        context = contexts[-1]
        path = f"context-selections/{attempt_id}/{context['artifact_digest'][7:]}.json"
        try:
            pilot.read_context_selection(root, attempt_id, context["artifact_digest"])
            record(context["artifact_digest"], "authorized-context", MARKER, path)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    if responses:
        response = responses[-1]
        stage_id, digest = response["stage_instance_id"], response["artifact_digest"]
        path = f"model-stage-artifacts/{attempt_id}/{digest[7:]}.json"
        try:
            publication = model_outputs.get(digest) or pilot.read_model_stage_artifact(root, attempt_id, stage_id, digest)
            next_artifact = {
                "context-marker": GENERATOR, "tc-generator": GENERATOR, "tc-reviewer": REVIEW_ANSWER, "tc-to-autotest": AUTOMATION_REVIEW,
                "autotest-reviewer": MATERIALIZE,
            }[stage_id.split(":", 1)[0]]
            if stage_id.startswith(("context-marker:requirements", "context-marker:batch-")):
                next_artifact = MARKER  # the remaining parts, then the merge by code
            if stage_id == "tc-generator:rework-1":
                next_artifact = REVIEW  # nobody reviews the rework before V7b: finish publishes r2
            elif stage_id.startswith("tc-generator:") and _batches_all_answered(root, attempt_id, responses):
                next_artifact = REVIEW  # the last answer assembled the candidate; prepare of the review publishes it from the journal
            if stage_id.startswith("autotest-reviewer:"):
                from tools.finalize_attempt import automation_next, correction_offered

                outcome = automation_next(root, attempt_id)[0]
                next_artifact = (MATERIALIZE if outcome == "ACCEPTED"
                                 else AUTOMATION if outcome == "PENDING" or correction_offered(root, attempt_id) else CLOSURE)
            marker_done = stage_id.startswith("context-marker:") and next_artifact == GENERATOR  # the plan may wait for the human here (scope pause)
            record(digest, stage_id, next_artifact, publication["path"], **({"after_marker": True} if marker_done else {}))
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    assembly = next((row for row in reversed(events) if row["event_type"] == "CANDIDATE_PUBLISHED" and row.get("stage_instance_id") == "assembly"), None)
    if assembly:
        # The journal has the candidate digest, but its bundle path arrives with
        # reviewer evidence. Do not claim to have reread an unknown bundle here.
        confirmed.append((assembly["seq"], {
            "last_confirmed_stage": "assembly-publication-event",
            "next_step": REVIEW,
            "evidence_path": str(root / "events" / f"{assembly['seq']:010d}.json"),
        }))
    ledger_events = [row for row in events if row["event_type"] == "ARTIFACT_READ_BACK" and row.get("batch_id") == "reviewer-ledger-v1"]
    aborted = False  # a review closed without a verdict leaves its request without an answer for good: nothing waits for it
    if ledger_events:
        digest = ledger_events[-1]["artifact_digest"]
        path = f"reviewer-session-ledgers/{attempt_id}/{digest[7:]}.json"
        try:
            ledger = pilot.read_reviewer_session_ledger(root, attempt_id, digest)
            lifecycle = pilot.reviewer_lifecycle_projection(ledger)
            next_artifact = REVIEW_ANSWER
            if lifecycle["pre_verdict_abort"] or lifecycle["authoritative_verdict"] == "REJECTED":
                next_artifact, aborted = CLOSURE, lifecycle["pre_verdict_abort"]
            record(digest, "canonical-review:" + ledger["status"], next_artifact, path, stop_reason=lifecycle["abort_reason"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    named_receipts = 0
    # These readers are read-only; the similarly named recovery helpers may write events.
    for kind, next_artifact in (
        ("rework-round", GENERATOR),
        ("effective-canonical", CLOSURE if attempt["policy_profile"] == "cases-only-v1" else AUTOMATION),
        ("execution-inputs", MATERIALIZE), ("generated-delta", MATERIALIZE),
        ("materialization-command", HUMAN_RUN), ("execution-receipt", CLOSURE), ("execution-trace", CLOSURE),
        ("disposition-receipt", CLOSURE), ("resume-validation", DRIFT),
    ):
        path = pilot._receipt_target(root, attempt_id, kind)
        if kind == "rework-round" and not pilot.rework_round_bound(state, attempt_id):
            continue  # a file the journal does not bind is no receipt and no damage (a cut submit leaves one: its repeat writes it again)
        if not path.exists():
            if kind == "effective-canonical":  # gone where the journal recorded an accepting verdict is a named loss, not «nothing yet»
                try:
                    pilot.effective_canonical_or_reason(root, attempt_id)
                except (OSError, KeyError, TypeError, ValueError):
                    unreadable(str(path.relative_to(root)))
                    named_receipts += 1
            continue
        try:
            receipt = pilot._read_attempt_receipt_with_state(project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK")
            value, facts = receipt["record"], {}
            if kind == "generated-delta" and value["delta"]["facts"]["completion"] != "COMPLETE":
                next_artifact, facts["stop_reason"] = CLOSURE, "MATERIALIZATION_INCOMPLETE"
            if kind == "execution-receipt":
                facts["verification"] = value["payload"]["verdict"]
                facts["execution_diagnostics"] = value["payload"].get("diagnostics", [])
                if value["payload"].get("execution_source"):  # a human ran the build — said wherever the result is shown
                    facts["execution_source"] = {**value["payload"]["execution_source"], "note": "результат запуска предоставлен человеком, пакет его не воспроизводил"}
            if kind == "resume-validation":
                facts["stop_reason"] = value.get("reason_code")
            record(receipt["digest"], kind, next_artifact, receipt["path"], **facts)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(str(path.relative_to(root)))
    lost = pilot.lost_attempt_receipts(root, state, attempt_id)
    if lost:
        # the journal read back a receipt of the attempt (its digest) and no file of the run carries that digest any more; the plan and the header
        # are named by their own path, a receipt of another kind (the journal does not say which) by its digest
        for generator_input, receipt_kind in (("plan", "batch-plan"), ("header", "generator-header")):
            try:
                pilot._generator_input(project, root, state, attempt_id, generator_input)
            except (OSError, KeyError, TypeError, ValueError):
                unreadable(str(pilot._receipt_target(root, attempt_id, receipt_kind).relative_to(root)))
                named_receipts += 1
        for digest in lost[named_receipts:]:
            result["evidence_errors"].append({"code": "EVIDENCE_UNVERIFIED", "path": f"{root}: квитанция {digest} из журнала без файла"})
    for kind, next_artifact in (("pre_finalization_trace", CLOSURE), ("finalization_receipt", CLOSURE), ("terminal_trace", CLOSURE)):
        path = root / "closure" / attempt_id / f"{kind}.json"
        if not path.exists():
            continue
        try:
            value = pilot._read_artifact(project, root, path, kind.replace("_", " "))
            value = pilot.read_closure_artifact(root, attempt_id, kind, value["digest"])
            facts = {"stop_reason": "FINALIZATION_INVALID"} if kind == "finalization_receipt" and not value["valid"] else {}
            record(value["digest"], kind, next_artifact, str(path.relative_to(root)), **facts)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(str(path.relative_to(root)))
    if confirmed:
        result.update(max(confirmed, key=lambda row: row[0])[1])
    pending = [row for row in events if row["event_type"] == "MODEL_REQUESTED" and not any(response.get("stage_instance_id") == row.get("stage_instance_id") for response in responses)
               and not (aborted and row.get("stage_instance_id") == "tc-reviewer:canonical")
               and not (row.get("stage_instance_id") in (REWORK_STAGE, REWORK_REVIEW_STAGE) and pilot.read_effective_canonical_if_present(root, attempt_id) is not None)]  # a rework closed unfinished waits for nothing
    if pending:
        request = pending[-1]
        path = f"model-requests/{attempt_id}/{request['artifact_digest'][7:]}.json"
        try:
            pilot.read_model_request(root, attempt_id, request["stage_instance_id"], request["artifact_digest"])
            result.pop("after_marker", None)
            result.update(next_step=answer_of(request["stage_instance_id"]), pending_request_path=str(root / path))
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    if result["evidence_errors"]:
        result.pop("after_marker", None)
        result.update(stop_reason="EVIDENCE_UNVERIFIED", next_step=DRIFT)
    elif attempt["state"] == "WAITING_FOR_INPUT" and result["stop_reason"] is None:
        result["stop_detail"] = "WAITING_FOR_INPUT has no persisted cause; consult the originating request."
    return result


# the receipts whose refused read status and report would pass in silence: the accepted set (a run of cases only has no other), the
# execution and the dispositions
RECEIPT_KINDS = ("effective-canonical", "execution-receipt", "disposition-receipt")


def receipts_warning(project: Path, root: Path, attempt_id: str | None) -> str | None:
    """``RUN_OF_OTHER_VERSION`` or ``RECEIPTS_UNREADABLE``: a receipt of the attempt (``RECEIPT_KINDS``) is on the disk and its strict
    read refuses it, so status and report leave out what it holds — the outcome of the review, the findings, the numbers of the run
    (handoff/v08/golden-mode-b-gap.md: 13 of 17 runs of cases only of the campaign do not read their accepted set).
    «Another version» only when the run recorded the fingerprint of its package (``skill_pack_digest`` of the marker's request) and it is
    not this package's; the same fingerprint, or none, says that the receipts are not read and not why (a changed or a broken file).
    Nothing when no such file is there, and nothing for a moved project: ``PROJECT_MOVED`` says why (a receipt binds its folder)."""
    from tools import pilot_state as pilot

    if not attempt_id:
        return None
    unread = False
    for kind in RECEIPT_KINDS:
        if pilot.receipt_target(root, attempt_id, kind).is_file():
            try:
                if kind == "effective-canonical":  # its own reader: its events are the publication and the read-back
                    pilot.read_effective_canonical(root, attempt_id)
                else:
                    pilot.read_attempt_receipt(root, attempt_id, kind, "ARTIFACT_READ_BACK")
            except (KeyError, OSError, TypeError, ValueError):
                unread = True
    if not unread or pilot.run_project_move(project, root) is not None:
        return None
    here = pilot.skill_pack_digest()
    return "RUN_OF_OTHER_VERSION" if here is not None and _run_pack(root, attempt_id) not in (None, here) else "RECEIPTS_UNREADABLE"


def _run_pack(root: Path, attempt_id: str) -> str | None:
    """The ``skill_pack_digest`` that the marker's request of the attempt recorded, or None: a run before the fingerprint, a run with no
    request of the marker, or a request that does not read."""
    from tools import pilot_state as pilot

    try:
        asked = next((event for event in pilot.derive_state(root)["events"] if event.get("attempt_id") == attempt_id
                      and event["event_type"] == "MODEL_REQUESTED" and str(event.get("stage_instance_id")).startswith("context-marker")), None)
        value = asked and pilot.read_model_request(root, attempt_id, str(asked["stage_instance_id"]), str(asked["artifact_digest"])).get("skill_pack_digest")
    except (KeyError, OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None

# Public names for run_pipeline, run_report and execution_core: the same objects under names without an underscore (П1).
durable_status_facts = _durable_status_facts
quarantine_summary = _quarantine_summary
review_notes_summary = _review_notes_summary
run_human_summary = _human_summary
style_notes = _style_notes
report_mismatch = _report_mismatch
