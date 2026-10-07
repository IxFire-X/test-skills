"""Analyst report of a driver run (opt-in ``next --analyst-report``, wave 2).

Reads the attempt's durable context-marker answer, every accepted review part result
(cases and automation; a rework attempt adds its parent's case review) and the survivor
triage decisions, and writes ``analyst-report.json``/``.md`` beside the attempt's
projections (``tools.analyst_report``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from tools import pipeline_driver as driver


def _review_parts(run_root: Path, attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    from tools.pilot_state import REWORK_RETRY_REASON, review_part_results

    owners = [str(attempt["attempt_id"])]
    if attempt.get("retry_reason") == REWORK_RETRY_REASON and attempt.get("parent_attempt_id"):
        owners.append(str(attempt["parent_attempt_id"]))
    rows = []
    for owner in owners:
        for key in ("canonical", "r1", "r2"):
            if owner != owners[0] and key != "canonical":
                continue
            for result in review_part_results(run_root, owner, key):
                rows.append({"review_key": key if owner == owners[0] else f"{key}@{owner[:8]}", "part_id": result["part_id"], "result": result})
    return rows


def report_for(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any]:
    from tools.analyst_report import build_report, marker_items, review_items, triage_items
    from tools.mutation_triage import decision_rows
    from tools.pipeline_driver_strength import answers

    attempt_id = str(attempt["attempt_id"])
    marker = driver._marker_artifact(run_root, attempt_id)
    items = [*marker_items(None if marker is None else marker["artifact"]), *review_items(_review_parts(run_root, attempt)),
             *triage_items(decision_rows(answers(run_root, attempt)))]
    return build_report(items, run_id=run_root.name, attempt_id=attempt_id, sources_of=_sources_of(run_root, attempt_id))


def _sources_of(run_root: Path, attempt_id: str) -> dict[str, list[str]]:
    """CREQ and case → SREQ of the attempt's effective (else candidate) document."""
    from tools.pilot_state import read_effective_canonical_if_present

    effective = read_effective_canonical_if_present(run_root, attempt_id)
    document = effective["document"] if effective else None
    if document is None:
        path = driver.work_dir(run_root) / "artifacts" / "candidate.json"
        document = driver._read_json(path) if path.is_file() else {}
    table: dict[str, list[str]] = {}
    for row in document.get("source_to_canonical_mappings", []):
        for creq in row.get("canonical_requirement_ids", []):
            table.setdefault(creq, []).append(row["source_requirement_id"])
    # A finding that names only cases (an automation reviewer often does) is keyed by their requirements.
    for case in document.get("test_cases", []):
        table[case["case_id"]] = sorted({source for creq in case.get("requirement_ids", []) for source in table.get(creq, [creq])})
    return table


def attempt_report(project: Path, run_id: str) -> dict[str, Any]:
    run_root = driver._run_root(project, run_id)
    return report_for(run_root, driver._attempt(run_root))


def analyst_summary(run_root: Path, attempt: Mapping[str, Any], summary: dict[str, Any]) -> None:
    """Write the report beside the attempt's projections when the run asked for it (summary 1.1.0)."""
    from tools.analyst_report import write_report

    if not driver._config(run_root).get("analyst_report"):
        return
    report = report_for(run_root, attempt)
    summary["schema_version"] = "1.1.0"
    summary["paths"].update(write_report(driver._bundle_dir(run_root, attempt), report))
    summary["analyst_questions"] = len(report["items"])
