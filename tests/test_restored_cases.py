"""updfix: a case the human brought in the CSV and the review rejected stays in the Zephyr export as the CSV had it (a tail after the accepted
cases); the report and the analyst's file name it by its Key; the accepted set, the autotests and the new cases of the model are as before."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from tests.test_cases_import_docs import _fragment, _to_plan
from tests.test_cases_import_new import _submit
from tests.test_partial_publication import PROFILE, _partial, _real, r1_document
from tools.cases_import import imported_value
from tools.finalize_attempt import split_cases
from tools.pilot_state import read_effective_canonical
from tools.publish_test_case_bundle import build_bundle
from tools.run_report import write_report
from tools.test_case_projections import _HEADERS, restored_csv
from tools.zephyr_template_export import ZephyrTemplateError

HEADER = ("Key,Name,Objective,Precondition,Priority,Folder,Labels,Test Script (Step-by-Step) - Step,Test Script (Step-by-Step) - Test Data,"
          "Test Script (Step-by-Step) - Expected Result")
CSV = (HEADER + "\r\n"
       "PC-T1,Склейка двух значений,Проверить склейку,Модуль доступен,High,/Склейка,smoke,Вызвать combine с «a» и «b»,\"left = \"\"a\"\"\nright = \"\"b\"\"\",Возвращена строка «a:b»\r\n"
       ",,,,,,,Сравнить результат с ожидаемым,—,\"Строки совпадают\n\nДлина строки 3\"\r\n"
       ",Склейка с пустым значением,Проверить склейку с пустой правой частью,,Normal,/Склейка,,Вызвать combine с «a» и пустой строкой,—,Возвращена строка «a:»\r\n")
LINKS = [{"row": 2, "source_requirement_ids": ["SREQ-0001", "SREQ-0003"], "verdict": "fits"},  # PC-T1, two steps
         {"row": 4, "source_requirement_ids": [], "reason": "пустая правая часть в документах не описана", "verdict": "fits"}]  # no Key, outside the documents
STAGE = "tc-generator:rework-1"
FINDING = "Нет проверки состояния."


def _rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], number: int, rounds: int = 1, profile: str = "local-pilot-v1", passes: bool = False) -> dict:
    """An import run whose review rejects case ``number`` (1-based; 3 is the new one); with a round of rework its generator gives it up, without one the set is published
    at once: the set is published."""
    from tests.test_review_canaries import _answer, _boundary_and_removed, _candidate, _finding, _open, _select
    from tests.test_stage_steps import _call
    from tools import orchestrate_test_case_revision as orchestrate

    import tests.test_cases_import_docs as docs

    scan, make = docs._scan, docs._project

    def project_with_template(path: Path, text: str) -> Path:  # the template of the project's Zephyr export is set before the scan, as a tester sets it
        from tests.test_zephyr_template import _template_bytes

        project = make(path, text)
        (project / "zephyr").mkdir()
        (project / "zephyr" / "stubs.csv").write_bytes(_template_bytes())
        skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
        (project / ".skillsrc").write_text(json.dumps({**skillsrc, "projection": {"zephyr": {"template": "zephyr/stubs.csv"}}}), encoding="utf-8")
        return project

    monkeypatch.setattr(docs, "_project", project_with_template)
    monkeypatch.setattr(docs, "CSV", CSV)
    monkeypatch.setattr(docs, "_scan", lambda project, patch, **flags: scan(project, patch, profile=profile, **flags))
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: rounds)
    run = _to_plan(tmp_path, monkeypatch, links=LINKS)
    run["run_root"] = run["root"]
    assert _submit(run, _fragment(run))[0] == 0
    _open(run, monkeypatch)
    target = _candidate(run)["test_cases"][number - 1]
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [target["case_id"]], FINDING)]
    selected = _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))
    if rounds:
        assert selected["status"] == "REWORK_PENDING"
        assert _call(capsys, "prepare", run["root"], STAGE)[0] == 0
        if passes:  # the generator hands the case back as it is and the second round accepts it: every case is back
            from tests.test_rework_review import _answer as second, _open as open_second
            from tests.test_stage_steps import _put

            _put(run["root"], STAGE, {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": [target]})
            assert _call(capsys, "submit", run["root"], STAGE)[0] == 0 and open_second(run, capsys)[0] == 0
            _put(run["root"], "tc-reviewer:rework-1", second(run))
            assert _call(capsys, "submit", run["root"], "tc-reviewer:rework-1")[0] == 0
        else:
            assert _call(capsys, "submit", run["root"], STAGE, "--abort", "REWORK_UNUSABLE")[0] == 0
    else:
        assert selected["status"] == "EFFECTIVE_SELECTED"
    return {**run, "target": target["case_id"]}


def _published(run: dict, suffix: str = ".zephyr-scale.csv") -> Path:
    [path] = (run["root"] / "published").glob("*" + suffix)
    return path


def _cases(data: bytes) -> list[dict]:
    """The cases of a CSV without the row numbers (they are the place in the file, not the case)."""
    from tools.zephyr_csv import parse_zephyr_csv

    return [{**{key: value for key, value in case.items() if key != "row"}, "steps": [{key: value for key, value in step.items() if key != "row"} for step in case["steps"]]}
            for case in parse_zephyr_csv(data)]


@pytest.mark.parametrize("number", [1, 2])
def test_a_rejected_imported_case_stays_in_the_export_cell_for_cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], number: int) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, number)
    original = _cases(CSV.encode("utf-8"))
    exported = _cases(_published(run).read_bytes())
    assert len(exported) == 3 and exported[-1] == original[number - 1]  # the accepted cases first, the human's case last, as it was
    assert exported[0] != original[number - 1] and [case["key"] for case in exported].count("PC-T1") == 1


def test_a_rejected_new_case_is_not_in_the_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 3)
    assert [case["title"] for case in _cases(_published(run).read_bytes())] == [case["title"] for case in _cases(CSV.encode("utf-8"))]


def test_the_accepted_set_and_the_autotests_do_not_see_the_restored_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 1)
    document = read_effective_canonical(run["root"], run["attempt_id"])["document"]
    assert run["target"] not in [case["case_id"] for case in document["test_cases"]] and len(document["test_cases"]) == 2
    assert json.loads(_published(run, ".json").read_text(encoding="utf-8")) == document
    assert run["target"] not in split_cases(document, {"artifacts": {"implementation_relations": []}})["summary"]  # the split reads this document only


def test_without_a_round_of_rework_the_first_review_publishes_the_same_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 1, rounds=0)
    assert _cases(_published(run).read_bytes())[-1] == _cases(CSV.encode("utf-8"))[0]


def _export(run: dict, capsys: pytest.CaptureFixture[str]) -> tuple[Path, dict]:
    from types import SimpleNamespace

    from tools.run_pipeline import cmd_finish
    from tools.zephyr_template_export import export_import_csv

    assert cmd_finish(SimpleNamespace(project=str(run["project"]), run=run["root"].name)) in (0, 1)  # the scan helper keeps the printing of the commands
    return _published(run), export_import_csv


def test_the_template_export_carries_the_tail_and_does_not_mark_it_automated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 1, profile="cases-only-v1")
    bundle, export = _export(run, capsys)
    result = export(run["project"], run["root"].name, bundle)
    assert result["status"] == "ok" and result["rows"] == 4  # case two, the new case, the two steps of PC-T1
    assert "PC-T1" in Path(result["output"]).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("spoil", [lambda data: data + "X-1,Допись агента,,,,,,Шаг,—,Результат\r\n", lambda data: data.replace("Склейка двух значений", "Подмена")], ids=["a row added", "the tail changed"])
def test_the_template_export_refuses_a_file_that_is_not_the_set_and_its_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], spoil) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 1, profile="cases-only-v1")
    bundle, export = _export(run, capsys)
    bundle.write_bytes(spoil(bundle.read_text(encoding="utf-8-sig")).encode("utf-8-sig"))
    with pytest.raises(ZephyrTemplateError) as refused:
        export(run["project"], run["root"].name, bundle)
    assert refused.value.code == "ZEPHYR_EXPORT_CASES"


@pytest.mark.parametrize(("number", "row", "name", "title"), [(1, 2, "PC-T1", "Склейка двух значений"), (2, 4, "Key нет", "Склейка с пустым значением")])
def test_the_report_and_the_analyst_name_the_rejected_case_of_the_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                                      number: int, row: int, name: str, title: str) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, number)
    write_report(run["project"], run["root"].name)
    report, markdown = (json.loads((run["root"] / "report.json").read_text(encoding="utf-8")), (run["root"] / "report.md").read_text(encoding="utf-8"))
    questions = (run["root"] / "questions-for-analyst.md").read_text(encoding="utf-8")
    kept = {"row": row, "key": "PC-T1" if number == 1 else None, "codes": ["REVIEW_NOTE"]}
    assert report["acceptance"]["restored"] == [kept] and report["import"]["verdicts"]["restored"] == [{"row": row, "key": kept["key"]}]
    assert row not in [item["row"] for item in report["import"]["verdicts"]["unchanged"]]
    assert f"Из CSV отклонено ревью и вошло в выгрузку прежним: {name} (строка {row}); коды замечаний: REVIEW_NOTE." in markdown
    assert "(2 кейса + 1 из CSV как были)" in markdown and "отклонены ревью, в выгрузке как в CSV: 1 (строки {row})".format(row=row) in markdown
    assert FINDING not in markdown + json.dumps(report, ensure_ascii=False) and title not in markdown + json.dumps(report, ensure_ascii=False)  # the texts are the analyst's only
    assert f"- строка {row}" + (", Key PC-T1" if number == 1 else "") + f" — отклонён ревью, в выгрузку вошёл прежним (как в CSV): «{title}»" in questions
    assert f"замечание ревьюера: {FINDING}" in questions and (number == 1) == ("Key нет: Zephyr создаст дубль" not in questions)


def test_a_run_without_an_import_has_no_tail_and_its_bytes_are_the_old_ones() -> None:
    document = r1_document()
    effective, partial = _partial(document, _real(document, 1))
    assert "restored" not in partial
    assert build_bundle(effective, PROFILE, partial=partial).csv_bytes == build_bundle(effective, PROFILE).csv_bytes


def test_a_case_that_passed_the_rework_leaves_no_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _rejected(tmp_path, monkeypatch, capsys, 1, passes=True)
    assert [case["key"] for case in _cases(_published(run).read_bytes())].count("PC-T1") == 1 and len(_cases(_published(run).read_bytes())) == 3
    assert not list((run["root"] / "published").glob("*REJECTED-CASES*"))


def test_the_tail_carries_the_label_of_an_obsolete_case() -> None:

    links = [{"row": 2, "source_requirement_ids": [], "reason": "требование удалено", "verdict": "obsolete"}, {"row": 4, "source_requirement_ids": [], "reason": "нет"}]
    [row, _other] = imported_value("cases/zephyr.csv", CSV.encode("utf-8"), links=links, requirements=[])["cases"]
    [first, *_steps] = list(csv.reader(io.StringIO(restored_csv([row]).decode("utf-8"), newline="")))
    assert first[_HEADERS.index("Labels")] == "smoke, устарел" and first[_HEADERS.index("Key")] == "PC-T1"
