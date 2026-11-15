"""v0.6 fix6: no free text of the model, the document or the agent's files in report.json / report.md; the report is written
for a closed run whose terminal result is spoiled."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_run_report import _finished_run

FREE = "Заказ клиента Иванова на 5000 руб сохранён"


def _both(run_root: Path) -> bytes:
    return (run_root / "report.json").read_bytes() + (run_root / "report.md").read_bytes()


# 1. the text of a document requirement stays in questions-for-analyst.md ----------------------------------------------

def test_a_gap_of_the_import_is_an_id_in_the_report_and_a_text_in_the_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import_docs import _to_plan
    from tools.run_report import write_report

    run = _to_plan(tmp_path, monkeypatch)
    write_report(run["project"], run["root"].name)
    report = json.loads((run["root"] / "report.json").read_text(encoding="utf-8"))
    assert report["import"]["gaps"] == [{"source_requirement_id": "SREQ-0002"}]
    assert "combine отказывает на None" not in _both(run["root"]).decode("utf-8")
    assert "SREQ-0002: combine отказывает на None." in (run["root"] / "questions-for-analyst.md").read_text(encoding="utf-8")


# 2. the names of the model's assertions ------------------------------------------------------------------------------

def test_an_expectation_hint_of_the_report_is_code_case_and_step_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_quality_hints import REJECTED, _assert, _document, _step
    from tools import run_report

    weak = _step("STEP-x-3", [_assert(FREE, "exists")], method="POST")
    document = _document([_step("STEP-x-1", [_assert("failed", "equals", True)], method="GET")], [_step("STEP-x-2", REJECTED, method="POST")], [weak])
    run_root = _finished_run(tmp_path)
    monkeypatch.setattr(run_report, "_effective_document", lambda root, attempt_id: (document, False))
    run_report.write_report(tmp_path, run_root.name)
    gaps = json.loads((run_root / "report.json").read_text(encoding="utf-8"))["expectations"]["gaps"]
    assert sorted(gap["code"] for gap in gaps) == ["TD_NEGATIVE_NO_STATE_CHECK", "TD_VAGUE_EXPECTATION", "TD_WEAK_SUCCESS"]
    assert all(set(gap) == {"code", "case_id", "step_id"} for gap in gaps), gaps
    assert FREE not in _both(run_root).decode("utf-8")


# 3. step-sizes.jsonl ----------------------------------------------------------------------------------------------------

def test_a_measurement_row_with_free_text_is_counted_and_not_printed(tmp_path: Path) -> None:
    from tools.run_report import write_report

    run_root = _finished_run(tmp_path)
    good = {"step": "tc-reviewer:canonical", "bytes": 10, "input_bytes": 9, "role_bytes": 1, "role": "tc-reviewer", "files": 2, "tokens_est": 3,
            "budget_bytes": 100, "fits": True, "warning": None, "at": "2026-10-28T01:00:00Z"}
    bad = {**good, "step": f"{FREE} | x\nстрока", "role": FREE, "warning": FREE, "at": FREE, "bytes": FREE, "tokens_est": [FREE], "fits": FREE, "files": FREE}
    (run_root / "step-sizes.jsonl").write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (good, bad)).encode("utf-8"))
    write_report(tmp_path, run_root.name)
    rows = json.loads((run_root / "report.json").read_text(encoding="utf-8"))["step_sizes"]
    assert rows[0] == good
    assert len(rows) == 2 and all(rows[1][key] is None for key in ("step", "role", "warning", "at", "bytes", "tokens_est", "fits", "files"))
    assert FREE not in _both(run_root).decode("utf-8")


@pytest.mark.parametrize("value", ["abc\n", "tc-reviewer:canonical\n", "a b", "", None, 7])
def test_an_identifier_is_the_whole_string_not_a_line_of_it(value: object) -> None:
    from tools.run_report import _identifier

    assert _identifier(value) is None
    assert _identifier("tc-reviewer:canonical") == "tc-reviewer:canonical"


def test_a_step_name_with_a_line_break_is_none_and_a_dash_in_the_table(tmp_path: Path) -> None:
    from tools.run_report import _step_size_lines, step_sizes
    from tools.step_size import RECORD

    lines = [{"step": "tc-reviewer:canonical\n| x", "bytes": 1}, {"step": "abc\n", "bytes": 2}]
    (tmp_path / RECORD).write_text("".join(json.dumps(row) + "\n" for row in lines), encoding="utf-8")
    rows = step_sizes(tmp_path)
    assert [row["step"] for row in rows] == [None, None] and [row["bytes"] for row in rows] == [1, 2]
    table = "\n".join(_step_size_lines({"step_sizes": rows}))
    assert "| — | 1 |" in table and "| — | 2 |" in table and "abc" not in table


# 4. zephyr-import.json --------------------------------------------------------------------------------------------------

def test_the_zephyr_import_of_the_report_names_only_columns_of_the_template_and_a_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_zephyr_template import _cases_only_run
    from tools.run_report import _zephyr_import
    from tools.zephyr_template_export import export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    export_import_csv(project, run_root.name, bundle_csv)
    record = json.loads((run_root / "zephyr-import.json").read_text(encoding="utf-8"))
    (run_root / "zephyr-import.json").write_text(json.dumps({**record, "columns_added": [FREE, *record["columns_added"]], "rows": FREE}), encoding="utf-8")
    shown = _zephyr_import(run_root)  # the whole report of this run is not readable: the template was put in the project after the scan
    assert shown["columns_added"] == record["columns_added"] and shown["rows"] is None
    assert FREE not in json.dumps(shown, ensure_ascii=False)


# 5. a spoiled terminal result ---------------------------------------------------------------------------------------------

def test_the_report_is_written_for_a_closed_run_with_a_spoiled_terminal_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _cases_only_effective, _finish
    from tools.run_pipeline import main

    run_root, _attempt_id = _cases_only_effective(tmp_path)
    project = run_root.parents[1]
    assert _finish(project, run_root, capsys)[1]["status"] == "ok"
    [terminal] = (run_root / "terminal-results").glob("*.json")
    terminal.write_bytes(terminal.read_bytes() + b" ")
    assert main(["report", "--project", str(project), "--run", run_root.name]) == 0, capsys.readouterr()
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    assert (run_root / "report.md").is_file() and report["stops"]["evidence_errors"] == ["EVIDENCE_UNVERIFIED"]


# 6. the count of source requirements ---------------------------------------------------------------------------------------

def test_the_source_requirements_are_those_of_the_document_not_of_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_report

    run_root = _finished_run(tmp_path)
    document = json.loads(next((run_root / "candidate-bundle").glob("*.json")).read_text(encoding="utf-8"))
    marked = run_report.build_report(tmp_path, run_root.name)[0]["counts"]["requirements"]
    document["source_requirements"] = document["source_requirements"][:-1]  # the plan of a run with an exclusion has no such requirement
    monkeypatch.setattr(run_report, "_effective_document", lambda root, attempt_id: (document, False))
    counts = run_report.build_report(tmp_path, run_root.name)[0]["counts"]
    assert counts["source_requirements"] == len(document["source_requirements"]) and counts["requirements"] == marked
