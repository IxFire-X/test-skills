"""FaT-REF stage 1: a Zephyr CSV of reference cases is refused in --docs, read by scan --reference into a receipt and
compared with the new cases as a review hint (CV_REFERENCE) — never context, generator input or canon."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_scan_guard import DOCS, _scan
from tests.test_first_run import _python_project


def _example() -> dict:
    from tests.test_review_view import _example as example

    return example()


def _csv(document: dict) -> bytes:
    from tools.test_case_projections import render_zephyr_csv

    return render_zephyr_csv(document).payload


def _reference(document: dict) -> list[dict]:
    from tools.zephyr_csv import parse_zephyr_csv

    return parse_zephyr_csv(_csv(document))


def test_the_same_cases_pair_one_to_one() -> None:
    from tools.reference_match import reference_match

    document = _example()
    match = reference_match(document, _reference(document))
    assert len(match["pairs"]) == len(document["test_cases"]) and match["gaps"] == match["new"] == match["duplicates"] == []
    assert all(pair["score"] == 1.0 for pair in match["pairs"])


def test_a_missing_case_is_a_gap_and_an_unrelated_case_is_new() -> None:
    from tools.reference_match import reference_label, reference_match

    document = _example()
    reference = _reference(document)
    fewer = copy.deepcopy(document)
    dropped = fewer["test_cases"].pop(0)  # its reference row has no other case of the same text
    unrelated = copy.deepcopy(fewer["test_cases"][0])
    unrelated.update(case_id="TC-NEW-1", title="Экспорт отчёта в PDF", objective="Проверить печать квитанции",
                     steps=[dict(step, action="Нажать кнопку печати квитанции") for step in unrelated["steps"]])
    fewer["test_cases"].append(unrelated)
    match = reference_match(fewer, reference)
    assert match["gaps"] == [reference_label(reference[0])] and match["new"] == ["TC-NEW-1"], match
    assert dropped["case_id"] not in [pair["case_id"] for pair in match["pairs"]]


def test_two_new_cases_on_one_reference_case_are_a_possible_duplicate() -> None:
    from tools.reference_match import reference_label, reference_match

    document = _example()
    reference = _reference(document)
    twice = copy.deepcopy(document)
    copy_case = copy.deepcopy(twice["test_cases"][0])
    copy_case["case_id"] = twice["test_cases"][0]["case_id"] + "-copy"
    twice["test_cases"].append(copy_case)
    assert reference_match(twice, reference)["duplicates"] == [reference_label(reference[0])]


def test_render_without_reference_is_byte_identical_and_with_it_counts_only() -> None:
    from tools.review_view import render

    document = _example()
    assert hashlib.sha256(render(document)).hexdigest() == "df245643eca8b66e8cc49f2c7fb1756465d9f9000cf78c79b654bf5d4a2a2b82"
    reference = _reference(document)
    reference[0]["title"] = "Совсем другой кейс про печать"
    reference[0]["objective"] = "Проверить печать"
    reference[0]["steps"] = [{"row": 2, "action": "Нажать печать", "test_data": "", "expected": ""}]
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    view = render(document, "review", reference=reference).decode("utf-8")
    line = next(text for text in view.splitlines() if text.startswith("- CV_REFERENCE"))
    assert "пробелы (эталон без пары) 1" in line and "Совсем другой" not in view and "Нажать печать" not in view
    assert json.dumps(document, ensure_ascii=False, sort_keys=True) == before


def test_a_zephyr_csv_in_docs_is_refused_with_the_reference_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline

    project = _python_project(tmp_path, runtime=False)
    (project / "services" / "api" / "docs" / "cases.csv").write_bytes(_csv(_example()))
    with pytest.raises(run_pipeline.HostStop) as stop:
        run_pipeline._docs_entries(project, ["services/api/docs/cases.csv"])
    assert stop.value.code == "DOCS_ZEPHYR_CSV" and "--reference" in stop.value.fix
    assert run_pipeline._docs_entries(project, [DOCS])  # a requirements document still goes


def test_scan_reference_publishes_a_receipt_and_keeps_it_out_of_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    project = _python_project(tmp_path, runtime=False)
    reference_path = "services/api/docs/reference.csv"
    data = _csv(_example())
    (project / reference_path).write_bytes(data)
    code, payload = _scan(project, monkeypatch, reference=reference_path)
    assert code == 0 and payload["context_summary"]["reference_cases"] == len(_example()["test_cases"]), payload
    root = project / ".pilot-runs" / payload["run_id"]
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    reference = pilot_state.read_reference_cases(root, attempt_id)
    assert reference["path"] == reference_path and reference["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()
    selected = [file["project_path"] for path in (root / "context-selections").rglob("*.json") for file in json.loads(path.read_bytes())["files"]]
    assert reference_path not in selected and selected, selected
    assert pilot_state.read_run(root)["manifest"]["scan_request"]["reference"] == reference_path


def test_scan_reference_that_is_not_a_zephyr_csv_is_refused_by_the_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=False)
    code, payload = _scan(project, monkeypatch, reference=DOCS)
    assert code == 2 and payload["reason"].startswith("ZEPHYR_CSV"), payload


def test_run_report_names_the_comparison_by_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import pilot_state, run_report

    document = _example()
    reference = _reference(document)[1:]
    monkeypatch.setattr(pilot_state, "read_reference_cases", lambda *_args: {"cases": reference})
    compared = run_report.reference(tmp_path, "a" * 32, document)
    assert compared["reference"] == len(reference) and len(compared["new"]) + len(compared["duplicates"]) == 1 and compared["gaps"] == []
    assert compared["pairs"] == len(reference)
    assert "CV_REFERENCE" in run_report._WARNING_TEXT


def test_own_v4_marker_names_the_machine_first_line_of_test_data() -> None:
    from tools.test_case_projections import _PROFILE_V4, render_zephyr_csv
    from tools.zephyr_csv import own_v4_marker, parse_zephyr_csv

    v4 = parse_zephyr_csv(render_zephyr_csv(_example(), _PROFILE_V4).payload)
    assert own_v4_marker(step["test_data"] for case in v4 for step in case["steps"]) in {"Операция:", "HTTP:", "Действие проекта:"}
    assert own_v4_marker(step["test_data"] for case in _reference(_example()) for step in case["steps"]) is None  # v5
    assert own_v4_marker(["HTTP: GET /owners — список владельцев", "Операция: создать заказ"]) is None  # human text


def test_scan_reference_in_the_v4_profile_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.test_case_projections import _PROFILE_V4, render_zephyr_csv

    project = _python_project(tmp_path, runtime=False)
    (project / "services/api/docs/v4.csv").write_bytes(render_zephyr_csv(_example(), _PROFILE_V4).payload)
    code, payload = _scan(project, monkeypatch, reference="services/api/docs/v4.csv")
    assert code == 2 and payload["reason"] == "IMPORT_CSV_V4" and "v5" in payload.get("fix", ""), payload
    assert "строка" in payload["detail"] and "первой строке Test Data" in payload["detail"], payload
