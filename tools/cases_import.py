"""FaT-IMP stage 1: ready test cases from a Zephyr CSV (``scan --cases``) become the source of a run.

The CSV is the requirement document of the run.  ``tools.api cases-import`` answers the marker step
``context-marker:requirements`` by code, not by a model: one source requirement per case, its text the case objective
(else its name) as written, provenance ``<csv>#строка N``, digest the sha256 of the CSV bytes; the journal records the
step with the model id ``controller:cases-import``.  A CSV exported by the package in profile v4 carries the machine
model in Test Data and is refused: machine text never becomes a human field.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv, require_human_csv

IMPORT_MODEL_ID = "controller:cases-import"
_FIXES = {
    "IMPORT_WITH_DOCS_LATER": "в ступени 1 импорт кейсов идёт без --docs: требования прогона — сами кейсы; уберите --docs",
    "IMPORT_CSV_V4": "выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — в Test Data должен быть текст человека",
    "ZEPHYR_CSV_COLUMNS": "нужен экспорт тест-кейсов Zephyr Scale в CSV с колонками шаблона; лишние колонки уберите из выгрузки",
    "ZEPHYR_CSV_ROWS": "исправьте названные строки CSV: у каждого кейса строка с Name и хотя бы один шаг",
    "ZEPHYR_CSV_ENCODING": "сохраните CSV в UTF-8 (Excel: Файл → Сохранить как → CSV UTF-8)",
    "ZEPHYR_CSV_NOT_CSV": "нужен экспорт тест-кейсов Zephyr Scale в CSV",
    "ZEPHYR_CSV_DELIMITER": "нужен CSV с разделителем «,» или «;» и колонками Zephyr (Name, …)",
    "IMPORT_NOT_CASES_RUN": "cases-import — для прогона scan --cases; у этого прогона требования размечает модель",
    "IMPORT_CSV_CHANGED": "CSV изменился после scan: повторите scan --cases --new-run",
}


class CasesImportError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.fix = code, _FIXES[code]
        super().__init__(f"{code}: {detail}")


def read_cases(data: bytes) -> list[dict[str, Any]]:
    """The cases of the CSV; a v4 export of the package is refused by the one check of REF and IMP (``require_human_csv``)."""
    try:
        cases = parse_zephyr_csv(data)
        require_human_csv(cases)
    except ZephyrCsvError as error:
        raise CasesImportError(error.code, str(error).split(": ", 1)[1]) from None
    return cases


def source_requirements(path: str, data: bytes, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return [{"source_requirement_id": f"SREQ-{number:04d}", "display_order": number,
             "text": case["objective"] if case["objective"].strip() else case["title"],
             "provenance": [f"{path}#строка {case['row']}"], "digest": digest}
            for number, case in enumerate(cases, start=1)]


def imported_value(path: str, data: bytes) -> dict[str, Any]:
    """The skeleton of the import: the human fields of every case as the CSV has them, one source requirement per case.

    Preconditions are one element (the cell), the expected result of a step is cut at blank lines (v5 joins the
    expectations with one), an empty priority is MEDIUM.  The model completes only the machine part around it."""
    from tools.batch_assembly import with_digest

    cases = read_cases(data)
    rows = []
    for requirement, case in zip(source_requirements(path, data, cases), cases):
        rows.append({
            "source_requirement_id": requirement["source_requirement_id"], "row": case["row"], "key": case["key"],
            "title": case["title"], "objective": case["objective"],
            "preconditions": [case["preconditions"]] if case["preconditions"].strip() else [],
            "priority": case["priority"] or "MEDIUM", "management": case["management"],
            "steps": [{"row": step["row"], "action": step["action"], "test_data": step["test_data"],
                       "expectations": [text for text in step["expected"].split("\n\n") if text.strip()]}
                      for step in case["steps"]],
        })
    return with_digest({"schema_version": "1.0.0", "csv": path, "csv_digest": "sha256:" + hashlib.sha256(data).hexdigest(), "cases": rows})


def run_csv(root: Path, attempt: Mapping[str, Any]) -> tuple[str, bytes] | None:
    """``(path, bytes)`` of the CSV of an import run, the bytes as the inventory of the attempt binds them; None otherwise."""
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt

    root = Path(root).resolve()
    path = cases_path(root)
    if path is None:
        return None
    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    row = next((item for item in inventory["files"] if item["project_path"] == path), None)  # bytes bound by the inventory
    data = (root.parents[1] / path).read_bytes()
    if row is None or row["content_digest"] != "sha256:" + hashlib.sha256(data).hexdigest():
        raise CasesImportError("IMPORT_CSV_CHANGED", path)
    return path, data


def cases_path(root: Path) -> str | None:
    manifest = json.loads((Path(root) / "run-manifest.json").read_text(encoding="utf-8"))
    value = (manifest.get("scan_request") or {}).get("cases")
    return str(value) if value else None


def import_requirements(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Answer ``context-marker:requirements`` by code from the CSV of the run; again on an answered step changes nothing."""
    from tools import pilot_state
    from tools.project_inventory import read_execution_baseline

    root = Path(run_root).resolve()
    path = cases_path(root)
    if path is None:
        raise CasesImportError("IMPORT_NOT_CASES_RUN", "у прогона нет --cases")
    state = pilot_state.derive_state(root)
    attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
    stage = "context-marker:requirements"
    answered = [event for event in state["events"] if event.get("attempt_id") == attempt_id
                and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == stage]
    if answered:
        result = dict(pilot_state.read_model_stage_artifact(root, attempt_id, stage, str(answered[-1]["artifact_digest"])))
        return {**result, "imported": pilot_state.publish_imported_cases(root, attempt_id)}
    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    path, data = run_csv(root, attempt)
    pilot_state.publish_imported_cases(root, attempt_id)  # the skeleton: the last tc-generator input
    artifact = {
        "schema_version": "5.0.0", "stage": "context-marker",
        "artifacts": {"analytics_documentation": {"requirements": source_requirements(path, data, read_cases(data))},
                      "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}},
        "warnings": [],
    }
    pilot_state.publish_model_request(root, attempt_id, stage, model_id=IMPORT_MODEL_ID, invocation_id=f"cases-import-{attempt_id[:12]}",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])
    published = dict(pilot_state.publish_model_stage_artifact(root, attempt_id, stage, artifact))
    published["warning"] = None  # the import is code, not a model answer
    published["imported"] = pilot_state.publish_imported_cases(root, attempt_id)
    return published
