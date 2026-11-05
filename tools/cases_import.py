"""Ready test cases from a Zephyr CSV (``scan --cases``) become the source of a run.

The CSV is the requirement document of the run.  ``tools.api cases-import`` answers the marker step
``context-marker:requirements`` by code, not by a model: one source requirement per case, its text the case objective
(else its name) as written, provenance ``<csv>#строка N``, digest the sha256 of the CSV bytes; the journal records the
step with the model id ``controller:cases-import``.  A CSV exported by the package in profile v4 carries the machine
model in Test Data and is refused: machine text never becomes a human field.

With ``--docs`` the model answers ``context-marker:requirements`` from the documents as in any run
and links every case of the CSV to the source requirements it checks (``analytics_documentation.case_links``), checked
before the write.  The merged marker keeps the linked requirements and adds one code-made requirement per case linked to
none («вне документов»); a document requirement no case links is a gap of the report, never a model-made case.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
from tools.fixes import FIXES
from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv, require_human_csv

IMPORT_MODEL_ID = "controller:cases-import"
_FIXES = {code: FIXES[code] for code in (
    "IMPORT_CASE_LINKS",
    "IMPORT_LINKS_NOT_ANSWERED",
    "IMPORT_CSV_V4",
    "ZEPHYR_CSV_COLUMNS",
    "ZEPHYR_CSV_ROWS",
    "IMPORT_CSV_SECRET",
    "ZEPHYR_CSV_ENCODING",
    "ZEPHYR_CSV_NOT_CSV",
    "ZEPHYR_CSV_DELIMITER",
    "IMPORT_NOT_CASES_RUN",
    "IMPORT_CSV_CHANGED",
    "IMPORT_REQUIREMENTS_INVALID",
)}  # the texts live in tools.fixes


class CasesImportError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.fix = code, _FIXES[code]
        super().__init__(f"{code}: {detail}")


_HTTP_LINE = re.compile(r"HTTP [1-5]\d\d ")
_BLANK = "\n\n"  # v5 joins the expectations of a step with one blank line


def split_expected(text: str) -> list[str]:
    """The expectations of a v5 Expected Result cell: v5 joins them with a blank line; an ``HTTP <code> <reason>`` part
    belongs to the expectation before it (the canonical HTTP form «result, blank line, status»)."""
    parts: list[str] = []
    for part in text.split(_BLANK):
        if parts and _HTTP_LINE.match(part):
            parts[-1] += _BLANK + part
        elif part.strip():
            parts.append(part)
    return parts


# a password as plain text in a cell of an import — key, «:» or «=», a value; only for the CSV of --cases
_CELL_PASSWORD = re.compile(r"(?i)(?<![\w-])(?P<key>пароль|password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*(?P<value>[^\s,;]+)")
_NOT_A_VALUE = re.compile(r"^(?:—|-|\*+|<[^<>]*>?)$")


def password_cells(cases: list[dict[str, Any]]) -> list[str]:
    """«строка N, колонка X: похоже на пароль (ключ …)» for each cell with an explicit secret value — never the value.
    A placeholder (``${VAR}``, ``<…>``, asterisks, «—») or a mention without a value («ввести пароль») is no secret; the
    value rule is the inventory's ``_secret_value``."""
    from tools.project_inventory import _secret_value

    found: list[str] = []
    for row, column, text in _cells(cases, ("Precondition",)):
        keys = [match.group("key") for match in _CELL_PASSWORD.finditer(text or "")
                if not _NOT_A_VALUE.match(match.group("value")) and _secret_value(match.group("value").encode("utf-8"))]
        if keys:
            found.append(f"строка {row}, колонка {column}: похоже на пароль (ключ «{keys[0]}»)")
    return found


def secret_cell(cases: list[dict[str, Any]], rule_id: str) -> str | None:
    """«строка N, колонка X» of the first cell the inventory's signature rule ``rule_id`` finds — never the value."""
    from tools.project_inventory import token_signature_rule

    for row, column, text in _cells(cases, ("Name", "Objective", "Precondition")):
        if token_signature_rule((text or "").encode("utf-8")) == rule_id:
            return f"строка {row}, колонка {column}"
    return None


def _cells(cases: list[dict[str, Any]], case_columns: tuple[str, ...]) -> list[tuple[int, str, str]]:
    fields = {"Name": "title", "Objective": "objective", "Precondition": "preconditions"}
    cells = [(case["row"], column, case[fields[column]]) for case in cases for column in case_columns]
    cells += [(step["row"], column, step[field]) for case in cases for step in case["steps"]
              for column, field in (("Step", "action"), ("Test Data", "test_data"), ("Expected Result", "expected"))]
    return sorted(cells, key=lambda cell: cell[0])


def read_cases(data: bytes) -> list[dict[str, Any]]:
    """The cases of the CSV; a v4 export of the package is refused by the one check of the reference and import readers (``require_human_csv``).

    A cell no faithful answer could keep is refused by row — an empty Step or Expected Result, a service mark
    as the expected result (the canonical rule ``service_expected``)."""
    from tools.canonical_document import service_expected

    try:
        cases = parse_zephyr_csv(data)
        require_human_csv(cases)
    except ZephyrCsvError as error:
        raise CasesImportError(error.code, str(error).split(": ", 1)[1]) from None
    errors: list[str] = []
    for case in cases:
        for step in case["steps"]:
            if not step["action"].strip():
                errors.append(f"строка {step['row']}: пустой Step")
            expectations = split_expected(step["expected"])
            if not expectations:
                errors.append(f"строка {step['row']}: пустой Expected Result")
            errors += [f"строка {step['row']}: служебный ожидаемый результат «{text.split(_BLANK)[0].strip()}»"
                       for text in expectations if service_expected(text.split(_BLANK)[0])]
    if errors:
        raise CasesImportError("ZEPHYR_CSV_ROWS", "; ".join(errors))
    return cases


def source_requirements(path: str, data: bytes, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return [{"source_requirement_id": f"SREQ-{number:04d}", "display_order": number,
             "text": case["objective"] if case["objective"].strip() else case["title"],
             "provenance": [f"{path}#строка {case['row']}"], "digest": digest}
            for number, case in enumerate(cases, start=1)]


OUTSIDE = "SREQ-CSV-"  # the requirement code makes for a case linked to no document requirement («вне документов»)


def case_sources(case: Mapping[str, Any]) -> list[str]:
    """The source requirements a skeleton case traces to — its links (stage 2) or its own row's one (stage 1)."""
    return list(case["source_requirement_ids"]) if "source_requirement_ids" in case else [str(case["source_requirement_id"])]


def canonical_management(case: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical management of an imported case — Key, folder, labels, custom fields as the CSV has them."""
    management = case["management"]
    return {"status": management["status"], "folder": management["folder"], "components": management["components"],
            "labels": management["labels"], "owner": management["owner"], "estimated_time": management["estimated_time"],
            "external_keys": {"zephyr_scale": case["key"]} if case["key"] else {},
            "external_links": {"issues": management["issues"], "pages": management["pages"]},
            "custom_fields": management["custom_fields"]}


def require_case_links(cases: list[dict[str, Any]], requirements: list[Mapping[str, Any]], links: Any) -> None:
    """One link per case row of the CSV, to source requirements of the answer; no requirements only with a reason."""
    known = {str(item["source_requirement_id"]) for item in requirements}
    rows = [case["row"] for case in cases]
    if not isinstance(links, list) or not all(isinstance(link, Mapping) for link in links):
        raise CasesImportError("IMPORT_CASE_LINKS", "нет analytics_documentation.case_links")
    given = [link.get("row") for link in links]
    errors = [f"строка {row}: нет связи" for row in rows if row not in given]
    errors += [f"строка {row}: связей {given.count(row)}" for row in dict.fromkeys(given) if given.count(row) > 1]
    errors += [f"строка {row}: не строка кейса CSV" for row in dict.fromkeys(given) if row not in rows]
    for link in links:
        ids = link.get("source_requirement_ids")
        if not isinstance(ids, list):
            errors.append(f"строка {link.get('row')}: нет source_requirement_ids")
            continue
        errors += [f"строка {link.get('row')}: {item} не SREQ ответа" for item in ids if item not in known]
        if not ids and not str(link.get("reason") or "").strip():
            errors.append(f"строка {link.get('row')}: пусто без reason")
    if errors:
        raise CasesImportError("IMPORT_CASE_LINKS", "; ".join(errors[:12]))


def marker_requirements(path: str, data: bytes, requirements: list[Mapping[str, Any]], links: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The requirements of the merged marker of an import with documents — the linked ones of the answer in its
    order, then one per case linked to none (text the objective, else the name, provenance ``<csv>#строка N``);
    ``display_order`` 1..N again, as the plan requires."""
    linked = {item for link in links for item in link["source_requirement_ids"]}
    kept = sorted((dict(item) for item in requirements if item["source_requirement_id"] in linked), key=lambda item: item["display_order"])
    by_row = {link["row"]: link for link in links}
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    outside = [{"source_requirement_id": f"{OUTSIDE}{case['row']}", "text": case["objective"] if case["objective"].strip() else case["title"],
                "provenance": [f"{path}#строка {case['row']}"], "digest": digest}
               for case in read_cases(data) if not by_row[case["row"]]["source_requirement_ids"]]
    return [{**item, "display_order": number} for number, item in enumerate([*kept, *outside], start=1)]


def gaps(requirements: list[Mapping[str, Any]], links: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The document requirements no case of the CSV links — the gaps of the import, for the report and the analyst."""
    linked = {item for link in links for item in link["source_requirement_ids"]}
    return [{"source_requirement_id": str(item["source_requirement_id"]), "text": str(item["text"])}
            for item in requirements if item["source_requirement_id"] not in linked]


def outside_rows(imported: Mapping[str, Any]) -> list[int]:
    """The CSV rows of the cases linked to no document requirement («вне документов»)."""
    return [int(case["row"]) for case in imported["cases"] if any(source.startswith(OUTSIDE) for source in case_sources(case))]


def imported_value(path: str, data: bytes, links: list[Mapping[str, Any]] | None = None,
                   requirements: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The skeleton of the import: the human fields of every case as the CSV has them, one source requirement per case.

    Preconditions are one element (the cell), the expected result of a step is cut at blank lines (v5 joins the
    expectations with one), an empty priority is MEDIUM.  The model completes only the machine part around it.

    With the links of an import with documents a case traces to ``source_requirement_ids`` (its links, else its
    «вне документов» requirement) and the skeleton names the ``gaps``; without links it is the skeleton of stage 1."""
    from tools.batch_assembly import with_digest

    cases = read_cases(data)
    by_row = {link["row"]: link for link in links or []}
    rows = []
    normalized: list[dict[str, Any]] = []  # the cells code fills in one way only; the check compares with them
    for requirement, case in zip(source_requirements(path, data, cases), cases):
        if not case["objective"].strip():
            normalized.append({"row": case["row"], "field": "objective"})
        normalized += [{"row": step["row"], "field": "test_data"} for step in case["steps"] if not step["test_data"].strip()]
        sources = ({"source_requirement_ids": list(by_row[case["row"]]["source_requirement_ids"]) or [f"{OUTSIDE}{case['row']}"]}
                   if links is not None else {"source_requirement_id": requirement["source_requirement_id"]})
        rows.append({
            **sources, "row": case["row"], "key": case["key"],
            "title": case["title"], "objective": case["objective"] if case["objective"].strip() else case["title"],
            "preconditions": [case["preconditions"]] if case["preconditions"].strip() else [],
            "priority": case["priority"] or "MEDIUM", "management": case["management"],
            "steps": [{"row": step["row"], "action": step["action"], "test_data": step["test_data"] if step["test_data"].strip() else "—",
                       "expectations": split_expected(step["expected"])}
                      for step in case["steps"]],
        })
    return with_digest({"schema_version": "1.0.0", "csv": path, "csv_digest": "sha256:" + hashlib.sha256(data).hexdigest(), "cases": rows,
                        **({"normalized": normalized} if normalized else {}),
                        **({"gaps": gaps(requirements or [], links)} if links is not None else {})})


def slice_value(imported: Mapping[str, Any], plan: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
    """The skeleton of one batch — the imported cases it owns, in the order of the plan, and the normalized cells
    of their rows only.  The last tc-generator input of that batch; the whole skeleton is no batch's input."""
    from tools.batch_assembly import with_digest

    batch = next((row for row in plan.get("batches") or [] if row.get("batch_id") == batch_id), None)
    if batch is None:
        raise ValueError(f"{batch_id} is not a batch of the plan {plan.get('digest')}")
    owned = batch["owned_source_requirement_ids"]
    order = {source: index for index, source in enumerate(owned)}
    cases = sorted((case for case in imported["cases"] if set(case_sources(case)) & set(owned)),  # by its first source
                   key=lambda case: (min(order.get(source, len(order)) for source in case_sources(case)), case["row"]))
    rows = {case["row"] for case in cases} | {step["row"] for case in cases for step in case["steps"]}
    normalized = [cell for cell in imported.get("normalized") or [] if cell["row"] in rows]
    return with_digest({"schema_version": "1.0.0", "csv": imported["csv"], "csv_digest": imported["csv_digest"], "plan_digest": plan["digest"],
                        "batch_id": batch_id, "cases": cases, **({"normalized": normalized} if normalized else {})})


def run_csv(root: Path, attempt: Mapping[str, Any]) -> tuple[str, bytes] | None:
    """``(path, bytes)`` of the CSV of an import run, the bytes as the inventory of the attempt binds them; None otherwise."""
    from tools import pilot_state
    from tools.project_inventory import read_inventory_receipt

    root = Path(root).resolve()
    path = cases_path(root)
    if path is None:
        return None
    baseline = pilot_state.frozen_baseline(root, attempt)
    inventory = read_inventory_receipt(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    row = next((item for item in inventory["files"] if item["project_path"] == path), None)  # bytes bound by the inventory
    secret = next((item for item in inventory.get("exclusions") or [] if item.get("project_path") == path and item.get("reason_code") == "SECRET_SUSPECTED"), None)
    if row is None and secret is not None:  # left out by a secret rule — not a changed file
        raise CasesImportError("IMPORT_CSV_SECRET", f"{path}: правило секретов {secret.get('rule_id')} — файл не вошёл в опись")
    data = (root.parents[1] / path).read_bytes()
    if row is None or row["content_digest"] != "sha256:" + hashlib.sha256(data).hexdigest():
        raise CasesImportError("IMPORT_CSV_CHANGED", path)
    return path, data


def _manifest(root: Path) -> dict[str, Any]:
    """The manifest of the run the strict way (sealed, with its schema, the boundary of the run) — not a second raw read of the file."""
    from tools import pilot_state

    return dict(pilot_state.read_run(Path(root))["manifest"])


def cases_path(root: Path) -> str | None:
    manifest = _manifest(root)
    value = (manifest.get("scan_request") or {}).get("cases")
    return str(value) if value else None


def requirements_artifact(path: str, data: bytes) -> dict[str, Any]:
    """The answer of ``context-marker:requirements`` code builds from the CSV of an import: one requirement per case."""
    return {
        "schema_version": "5.0.0", "stage": "context-marker",
        "artifacts": {"analytics_documentation": {"requirements": source_requirements(path, data, read_cases(data))},
                      "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}},
        "warnings": [],
    }


def is_code_requirements(root: Path, attempt: Mapping[str, Any], value: Mapping[str, Any]) -> bool:
    """A fact of the journal, not a label — an import without documents whose requirements answer is byte for
    byte the one code builds from the CSV the inventory binds.  Such requirements are the cases, not an OpenSpec reading."""
    root = Path(root).resolve()
    if cases_path(root) is None:
        return False
    manifest = _manifest(root)
    if (manifest.get("scan_request") or {}).get("docs"):
        return False
    try:
        found = run_csv(root, attempt)
    except (CasesImportError, OSError, ValueError):
        return False
    return found is not None and dict(value) == requirements_artifact(*found)


def docs_run(root: Path) -> bool:
    """An import with documents (``scan --cases`` with ``--docs``) — the model answers the requirements step."""
    if cases_path(root) is None:
        return False
    manifest = _manifest(root)
    return bool((manifest.get("scan_request") or {}).get("docs"))


def requirements_answer(root: Path, attempt_id: str) -> Mapping[str, Any]:
    """The recorded answer of ``context-marker:requirements``; refused while there is none."""
    from tools import pilot_state

    answered = [event for event in pilot_state.derive_state(root)["events"] if event.get("attempt_id") == attempt_id
                and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == "context-marker:requirements"]
    if not answered:
        raise CasesImportError("IMPORT_LINKS_NOT_ANSWERED", "нет ответа context-marker:requirements")
    return pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", str(answered[-1]["artifact_digest"]))["artifact"]


def answer_links(answer: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    documentation = answer["artifacts"]["analytics_documentation"]
    return list(documentation["requirements"]), list(documentation.get("case_links") or [])


def run_skeleton(root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """The skeleton of an import run as its CSV (and, with documents, the links of the requirements answer) gives it."""
    found = run_csv(root, attempt)
    if found is None:
        return None
    if not docs_run(root):
        return imported_value(*found)
    requirements, links = answer_links(requirements_answer(root, str(attempt["attempt_id"])))
    return imported_value(*found, links=links, requirements=requirements)


def run_marker_requirements(root: Path, attempt: Mapping[str, Any], answer: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """The requirements of the merged marker of an import with documents; None for any other run (the answer's own)."""
    if not docs_run(root):
        return None
    found = run_csv(root, attempt)
    requirements, links = answer_links(answer)
    return marker_requirements(*found, requirements, links)


def check_requirements_answer(root: Path, attempt: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    """The links of a model answer of the requirements step of an import with documents, before the write."""
    if not docs_run(root):
        return
    path, data = run_csv(root, attempt)
    requirements = value["artifacts"]["analytics_documentation"]["requirements"]
    require_case_links(read_cases(data), requirements, value["artifacts"]["analytics_documentation"].get("case_links"))


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
    if docs_run(root):  # the model answered the requirements and the links; code publishes the skeleton only
        requirements_answer(root, attempt_id)
        return {"imported": pilot_state.publish_imported_cases(root, attempt_id)}
    answered = [event for event in state["events"] if event.get("attempt_id") == attempt_id
                and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == stage]
    if answered:
        result = dict(pilot_state.read_model_stage_artifact(root, attempt_id, stage, str(answered[-1]["artifact_digest"])))
        return {**result, "imported": pilot_state.publish_imported_cases(root, attempt_id)}
    baseline = pilot_state.frozen_baseline(root, attempt)
    path, data = run_csv(root, attempt)
    pilot_state.publish_imported_cases(root, attempt_id)  # the skeleton: the last tc-generator input
    artifact = requirements_artifact(path, data)
    try:  # the answer is checked before its request is written — a refusal leaves no orphan request behind
        pilot_state.check_model_stage_artifact("context-marker:requirements", artifact)
    except ValueError as error:
        raise CasesImportError("IMPORT_REQUIREMENTS_INVALID", str(error)) from None
    pilot_state.publish_code_step_request(root, attempt_id, stage, model_id=IMPORT_MODEL_ID, invocation_id=f"cases-import-{attempt_id[:12]}",
                                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])  # an import cut here goes on
    published = dict(pilot_state.publish_model_stage_artifact(root, attempt_id, stage, artifact))
    published["warning"] = None  # the import is code, not a model answer
    published["imported"] = pilot_state.publish_imported_cases(root, attempt_id)
    return published
