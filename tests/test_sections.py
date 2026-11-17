"""sections: a document of many numbers is led by sections in one request.

``ready`` and the plan name the runs by sections before any model call, a run of a section is judged by the lines of the section and reads the rest of the
document as context, and one command says where the runs stand and puts their Zephyr CSV into one.  By the public commands (``ready``, ``scan``, ``prepare``,
``submit``, ``tools.assemble_sections``)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_openspec_selection import _scan
from tests.test_requirement_labels import DOC, _marker, _project
from tools import api
from tools.ready import check


def _doc(*sizes: int) -> str:
    """A document of chapters, each of ``size`` numbered statements ``<chapter>.1.<n>.``."""
    out = "# Требования\n\nВводный текст без номеров.\n\n"
    for chapter, size in enumerate(sizes, start=1):
        out += f"## {chapter}. Глава {chapter}\n\n" + "".join(f"{chapter}.1.{n}. Утверждение {chapter}.{n}.\n\n" for n in range(1, size + 1))
    return out


def _size_row(project: Path, docs: list[str], profile: str = "cases-only-v1") -> dict | None:
    return next((row for row in check(project, docs, profile=profile)["checks"] if row["id"] == "docs:size"), None)


def _lines_of(text: str, chapter: int) -> list[int]:
    return [number for number, line in enumerate(text.splitlines(), start=1) if re.match(rf"{chapter}\.1\.\d+\. ", line)]


def _marker_at(scan: dict, project: Path, lines: list[int], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    import hashlib

    run_root = str(scan["run_root"])
    assert api.main(["prepare", "--run-root", run_root, "--stage", "context-marker:baseline"]) == 0
    slot = Path(json.loads(capsys.readouterr().out)["result"]["answer_slot"])
    digest = "sha256:" + hashlib.sha256((project / DOC).read_bytes()).hexdigest()
    rows = [{"source_requirement_id": f"SREQ-{n:04d}", "display_order": n, "text": f"Требование {n}", "provenance": [f"{DOC}:{line}"], "digest": digest} for n, line in enumerate(lines, start=1)]
    slot.write_text(json.dumps({"schema_version": "5.0.0", "stage": "context-marker", "warnings": [],
                                "artifacts": {"analytics_documentation": {"requirements": rows}, "source_code_and_diff": {"sources": ["(none) — no authorized source files"]}}}), encoding="utf-8")
    code = api.main(["submit", "--run-root", run_root, "--stage", "context-marker:baseline"])
    return code, json.loads(capsys.readouterr().out)


# --- ready: the sections, before any model -------------------------------------------------------------------------------------------

def test_ready_names_the_runs_by_sections_for_a_document_of_many_numbers(tmp_path: Path) -> None:
    row = _size_row(_project(tmp_path, _doc(25, 25, 20)), [DOC])
    assert row and row["level"] == "WARN"
    assert row["message"].count(f'--docs "{DOC}#') == 3 and "70 номерах" in row["message"] and "прогон 1 (25 ном.)" in row["message"]


def test_ready_prints_no_size_row_where_the_set_fits(tmp_path: Path) -> None:
    assert _size_row(_project(tmp_path, _doc(30)), [DOC]) is None


def test_ready_prints_no_size_row_for_a_section_within_the_limit(tmp_path: Path) -> None:
    assert _size_row(_project(tmp_path, _doc(25, 25, 20)), [f"{DOC}#1. Глава 1"]) is None


def test_ready_takes_small_neighbours_into_one_run(tmp_path: Path) -> None:
    row = _size_row(_project(tmp_path, _doc(10, 10, 10, 10)), [DOC])
    assert row and "прогон 1 (30 ном.)" in row["message"] and "прогон 2 (10 ном.)" in row["message"]
    assert row["message"].count(f'--docs "{DOC}#') == 4


def test_ready_goes_down_to_the_subsections_of_a_chapter_over_the_limit(tmp_path: Path) -> None:
    text = "# Т\n\n## Глава\n\n### Часть А\n\n" + "".join(f"1.1.{n}. Утверждение.\n\n" for n in range(1, 26)) + "### Часть Б\n\n" + "".join(f"1.2.{n}. Утверждение.\n\n" for n in range(1, 26))
    row = _size_row(_project(tmp_path, text), [DOC])
    assert row and f'--docs "{DOC}#Часть А"' in row["message"] and f'--docs "{DOC}#Часть Б"' in row["message"]


def test_ready_does_not_print_a_heading_the_shell_would_read_otherwise(tmp_path: Path) -> None:
    row = _size_row(_project(tmp_path, _doc(25, 25, 20).replace("## 1. Глава 1", '## 1. Глава "А"')), [DOC])
    assert row and "набери аргумент сам" in row["message"] and f'--docs "{DOC}#2. Глава 2"' in row["message"] and 'Глава "А"""' not in row["message"]


def test_ready_says_when_no_heading_tells_the_numbers_apart(tmp_path: Path) -> None:
    text = "# Требования\n\n" + "".join(f"1.1.{n}. Утверждение.\n\n" for n in range(1, 41))
    row = _size_row(_project(tmp_path, text), [DOC])
    assert row and "разделить нечем" in row["message"] and "--docs" not in row["message"]


def test_the_review_of_a_set_over_the_step_budget_is_a_hard_estimate_and_of_the_autotests_a_lower_one(tmp_path: Path) -> None:
    from tools.build_context import large_document

    project = _project(tmp_path, _doc(38))
    documents = {DOC: (project / DOC).read_text(encoding="utf-8")}
    assert large_document(project, documents, {}, False)[0] is False  # 38 × 8 000 B is over the checked size, within the step
    assert large_document(project, documents, {}, True)[0] is True  # 38 × 13 000 B is over the input of the autotest review


# --- the plan: the stop before the generator ------------------------------------------------------------------------------------------

def _plan(scan: dict, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    run_root = str(scan["run_root"])
    assert api.main(["prepare", "--run-root", run_root, "--stage", "plan"]) == 0
    slot = Path(json.loads(capsys.readouterr().out)["result"]["answer_slot"])
    slot.write_text(json.dumps({"subject": "Требования"}), encoding="utf-8")
    code = api.main(["submit", "--run-root", run_root, "--stage", "plan"])
    return code, json.loads(capsys.readouterr().out)


def test_the_plan_of_a_set_the_review_cannot_hold_is_refused_before_the_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(25, 25, 20))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 70, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 2 and "PLAN_REVIEW_TOO_LARGE" in json.dumps(out, ensure_ascii=False), out
    assert f'--docs "{DOC}#' in out["fix"]
    assert not list((Path(scan["run_root"]) / "batch-plans").glob("*.json"))


def test_the_plan_of_a_document_without_numbers_counts_the_requirements_of_the_marker_and_says_it_cannot_be_divided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, "# Требования\n\n" + "".join(f"Система делает действие {n}.\n\n" for n in range(1, 71)))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 70, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 2 and "PLAN_REVIEW_TOO_LARGE" in json.dumps(out, ensure_ascii=False) and "70 номерах" in json.dumps(out, ensure_ascii=False), out
    assert "разделить нечем" in out["fix"] and "--docs" in out["fix"] and not list((Path(scan["run_root"]) / "batch-plans").glob("*.json"))


def _import_plan_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cases: int) -> list[str]:
    """The notes of the plan of an import without documents of ``cases`` cases (the requirements are the cases of the CSV, by code)."""
    from tests.test_cases_import import HEADER, _project as import_project, _scan as import_scan

    rows = "".join(f"Кейс {n},Цель {n},,High,Шаг {n},—,Итог {n}\r\n" for n in range(1, cases + 1))
    project = import_project(tmp_path, HEADER + "\r\n" + rows)
    code, scan = import_scan(project, monkeypatch, profile="cases-only-v1")
    assert code == 0, scan
    run_root = str(scan["run_root"])
    assert api.main(["prepare", "--run-root", run_root, "--stage", "context-marker:requirements"]) == 0  # the requirements are the cases, by code
    stored = json.loads((Path(run_root) / json.loads(capsys.readouterr().out)["result"]["path"]).read_text(encoding="utf-8"))
    value = stored["artifact"] if "artifact" in stored else stored
    assert api.main(["prepare", "--run-root", run_root, "--stage", "context-marker:batch-001"]) == 0
    slot = Path(json.loads(capsys.readouterr().out)["result"]["answer_slot"])
    slot.write_text(json.dumps({**value, "artifacts": {**value["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine склеивает два значения"]}}}), encoding="utf-8")
    assert api.main(["submit", "--run-root", run_root, "--stage", "context-marker:batch-001"]) == 0
    capsys.readouterr()
    code, out = _plan(scan, capsys)
    assert code == 0, out
    return out["result"]["notes"]


def test_the_plan_of_an_import_without_documents_of_60_cases_is_not_refused_and_not_warned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert not any("ВНИМАНИЕ" in note for note in _import_plan_notes(tmp_path, monkeypatch, capsys, 60))  # 60 × 4 500 B: the zone where the control errors were caught


def test_the_plan_of_an_import_over_the_checked_size_warns_with_the_advice_for_a_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    notes = _import_plan_notes(tmp_path, monkeypatch, capsys, 70)
    assert any("ВНИМАНИЕ" in note and "из Zephyr меньше кейсов за раз (до 66" in note and "по файлам" not in note for note in notes), notes


def test_the_advice_for_a_set_of_openspec_areas_is_fewer_areas(tmp_path: Path) -> None:
    from tools.build_context import OPENSPEC_ADVICE, large_document

    found = large_document(_project(tmp_path, "# Т\n"), {}, {"openspec/specs": []}, False, requirements=70, openspec=True)
    assert found and found[0] is True and found[2] == OPENSPEC_ADVICE and "openspec/specs/<область>" in found[2]


def test_the_plan_of_a_set_over_the_checked_size_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(20, 20))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 40, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 0 and any(note.startswith("ВНИМАНИЕ") and "проверялся слабее" in note for note in out["result"]["notes"]), out


def test_the_plan_of_a_set_that_fits_has_no_new_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(30))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 30, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 0 and not any("ВНИМАНИЕ" in note for note in out["result"]["notes"]), out


# --- a run of a section: the numbers of the section, the rest of the document is context -----------------------------------------------

def test_a_marker_of_headings_for_a_section_of_many_numbers_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _doc(12, 12)
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    code, out = _marker_at(scan, project, _lines_of(text, 1)[:3], capsys)
    assert code == 2 and "CONTEXT_MARKER_REQUIREMENT_LABELS" in json.dumps(out, ensure_ascii=False) and "меток 12" in json.dumps(out, ensure_ascii=False), out


def test_a_marker_of_a_requirement_per_number_of_the_section_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _doc(12, 12)
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    assert _marker_at(scan, project, _lines_of(text, 1), capsys)[0] == 0


def test_a_requirement_from_a_line_outside_the_named_section_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _doc(12, 12)
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    code, out = _marker_at(scan, project, _lines_of(text, 1)[:11] + _lines_of(text, 2)[:1], capsys)
    assert code == 2 and "CONTEXT_MARKER_REQUIREMENT_OUTSIDE_SECTION" in json.dumps(out, ensure_ascii=False), out


def test_the_file_of_a_section_run_is_in_the_context_receipts_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, _doc(12, 12))
    scan = _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    paths = [item["project_path"] for path in (Path(scan["run_root"]) / "context-selections").rglob("*.json") for item in json.loads(path.read_text(encoding="utf-8")).get("files", [])]
    assert DOC in paths


def test_several_sections_of_one_file_are_one_entry_that_holds_each_and_nothing_else(tmp_path: Path) -> None:
    from tools.run_pipeline import _docs_entries

    text = _doc(3, 3, 3)
    project = _project(tmp_path, text)
    (entry,) = _docs_entries(project, [f"{DOC}#1. Глава 1", f"{DOC}#3. Глава 3", f"{DOC}#1. Глава 1"])
    assert entry["section"] == "1. Глава 1 || 3. Глава 3" and "1.1.1." in entry["content"] and "3.1.3." in entry["content"] and "2.1.1." not in entry["content"]
    assert entry["lines"].count("-") == 2 and "разделы" in entry["view"].splitlines()[0]


def test_a_file_named_whole_and_by_a_section_is_refused(tmp_path: Path) -> None:
    from tools.run_pipeline import HostStop, _docs_entries

    project = _project(tmp_path, _doc(3, 3))
    with pytest.raises(HostStop) as stopped:
        _docs_entries(project, [DOC, f"{DOC}#1. Глава 1"])
    assert stopped.value.code == "TASK_DOC_SECTION_CONFLICT"


# --- the labels: a number of a section and a name are no requirements ----------------------------------------------------------------

def test_the_number_of_a_section_is_no_label_where_the_headings_lost_their_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = "# Требования\n\n" + "".join(f"1.{k} Раздел {k}\n\n" + "".join(f"1.{k}.{j} Подраздел\n\n1.{k}.{j}.1 Требование {k}.{j}.\n\n" for j in range(1, 5)) for k in range(1, 4))
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 12, capsys)[0] == 0  # 27 labels with the numbers of the sections, 12 requirements


def test_a_name_with_one_label_is_no_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    glossary = "".join(f"{name} — стандарт.\n" for name in ("UTF-8", "SHA-256", "ISO-8601", "TLS-1.2", "AES-256", "RSA-2048", "JWT-2", "PDF-1", "XML-1"))
    project = _project(tmp_path, "# Требования\n\n" + glossary + "\n" + "".join(f"FR-{n:02d}. Поле {n} обязательно.\n" for n in range(1, 9)))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 8, capsys)[0] == 0  # 17 forms, 8 requirements


def test_a_prefix_of_many_labels_and_a_chain_of_numbers_still_count() -> None:
    from tools.build_context import counted_labels

    assert [label for _line, label in counted_labels("FR-01. а\nFR-02. б\nUTF-8 в\n1.1.1. г\n1.1.2. д\n")] == ["FR-01", "FR-02", "1.1.1", "1.1.2"]


# --- the report without a review -----------------------------------------------------------------------------------------------------

def test_the_warning_of_the_numbers_stands_in_a_report_whose_review_did_not_take_place() -> None:
    from tools.run_report import _acceptance_lines

    lines = _acceptance_lines({"requirement_labels": {"documents": [{"path": DOC, "labels": 35}], "requirements": 15}})
    assert len(lines) == 2 and lines[0].startswith("> **Внимание: в документе") and "номеров 35, требований в разметке 15" in lines[0]
    assert _acceptance_lines({}) == []


# --- one command: where the runs stand, and the one CSV ------------------------------------------------------------------------------

def _real_bundle(document: dict) -> dict:
    """The receipt the publication of *document* gives (names, profile and digests of its bundle), as ``publish_bundle`` would."""
    import hashlib

    from tools.canonical_document import document_sha256
    from tools.publish_test_case_bundle import _PROFILE, build_bundle

    bundle, name = build_bundle(document), f"published/{document['document_id']}.r{document['revision']}"
    digest = lambda data: "sha256:" + hashlib.sha256(data).hexdigest()  # noqa: E731
    return {"document_id": document["document_id"], "revision": document["revision"], "csv_profile": _PROFILE, "json_path": name + ".json", "preview_path": name + ".html",
            "csv_path": name + ".zephyr-scale.csv", "document_sha256": document_sha256(document), "preview_sha256": digest(bundle.preview_bytes), "csv_sha256": digest(bundle.csv_bytes)}


def _closed_run(project: Path, capsys: pytest.CaptureFixture[str], *, close: bool = True) -> Path:
    """A real cases-only run of the project whose effective set is published to ``<run>/published``; closed by ``finish`` when asked."""
    from unittest import mock

    from tests.test_finish import _cases_only_effective, _finish
    from tools import pilot_state
    from tools.publish_test_case_bundle import publish_bundle

    with mock.patch("tests.test_generated_delta._effective_bundle", _real_bundle):  # the receipt of the publication of the fixture is made up; here it is the real one
        run_root, attempt_id = _cases_only_effective(project)
    if close:
        publish_bundle(pilot_state.read_effective_canonical(run_root, attempt_id)["document"], run_root / "published")
        _finish(project, run_root, capsys)
    return run_root


def _csv(run_root: Path) -> bytes:
    return next((run_root / "published").glob("*.zephyr-scale.csv")).read_bytes()


def test_the_csv_of_two_runs_is_the_two_joined_under_one_header(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.assemble_sections import assemble

    first, second = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys)
    result = assemble(tmp_path, [first.name, second.name])
    head, _, rest = _csv(first).removeprefix(b"\xef\xbb\xbf").partition(b"\r\n")
    assert Path(result["output"]).read_bytes() == b"\xef\xbb\xbf" + head + b"\r\n" + rest + _csv(second).removeprefix(b"\xef\xbb\xbf").partition(b"\r\n")[2]
    assert Path(result["output"]).parent == second / "assembled" and result["cases"] > 0 and len(result["runs"]) == 2


def test_a_run_whose_set_carries_a_rejected_case_of_the_human_is_assembled_with_its_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_restored_cases import _export, _published, _rejected
    from tools.assemble_sections import assemble

    run = _rejected(tmp_path, monkeypatch, capsys, 1, profile="cases-only-v1")
    _export(run, capsys)  # closes the run
    result = assemble(run["project"], [run["root"].name])
    assert Path(result["output"]).read_bytes() == _published(run).read_bytes() and "PC-T1" in Path(result["output"]).read_text(encoding="utf-8-sig")


def test_a_csv_changed_after_the_publication_is_refused_and_nothing_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.assemble_sections import AssembleRefused, assemble

    first, second = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys)
    target = next((first / "published").glob("*.zephyr-scale.csv"))
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(AssembleRefused) as refused:
        assemble(tmp_path, [first.name, second.name])
    assert refused.value.code == "ASSEMBLE_CSV_MISMATCH" and first.name in str(refused.value) and not (second / "assembled").exists()


def test_a_run_without_a_set_is_named_in_the_summary_and_left_out_of_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.assemble_sections import assemble

    closed, open_run = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys, close=False)
    result = assemble(tmp_path, [closed.name, open_run.name])
    summary = json.loads(Path(result["summary"]).read_text(encoding="utf-8"))
    assert [row["csv"] for row in summary["runs"]] == ["included", "none"] and summary["runs"][1]["code"] == "NOT_CLOSED"
    assert Path(result["output"]).read_bytes() == _csv(closed)


def test_the_summary_counts_the_repeated_titles_and_holds_no_title(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state
    from tools.assemble_sections import assemble

    first, second = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys)
    titles = [case["title"] for case in pilot_state.read_effective_canonical(second, pilot_state.derive_state(second)["attempts"][-1]["attempt_id"])["document"]["test_cases"]]
    result = assemble(tmp_path, [first.name, second.name])
    text = Path(result["summary"]).read_text(encoding="utf-8")
    summary = json.loads(text)
    assert [row["title_repeats"] for row in summary["runs"]] == [0, len(titles)] and summary["title_repeats"] == len(titles)
    assert not any(title in text for title in titles)


def test_the_runs_of_the_project_stay_what_they_were_after_the_assembly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.assemble_sections import assemble
    from tools.run_pipeline import main

    first, second = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys)

    def seen() -> tuple[str, str]:
        main(["status", "--project", str(tmp_path), "--run", second.name])
        status = capsys.readouterr().out
        main(["report", "--project", str(tmp_path), "--run", second.name])
        return status, capsys.readouterr().out

    before = seen()
    assemble(tmp_path, [first.name, second.name])
    assert seen() == before


def test_a_run_that_was_assembled_is_still_a_source_of_reuse(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_marker_reuse import _fresh, _marker as marker_of, _request, _source
    from tools.assemble_sections import assemble

    closed = _closed_run(tmp_path, capsys)
    source, attempt = _source(tmp_path)
    _marker_answer, digest = marker_of(source, attempt)
    assemble(tmp_path, [closed.name, source.name])  # the files are put below the source run
    assert any((source / "assembled").iterdir())
    run_root, attempt_id, inputs = _fresh(tmp_path)
    assert _request(run_root, attempt_id, inputs, source.name)["reuse"]["response_digest"] == digest


def test_the_command_says_which_sections_have_a_run_and_refuses_the_assembly_while_one_has_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.assemble_sections import AssembleRefused, assemble, where

    project = _project(tmp_path, _doc(25, 25, 20))
    _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    states = {row["section"]: row["state"] for row in where(project, DOC)["sections"]}
    assert states == {"1. Глава 1": "no_csv", "2. Глава 2": "not_started", "3. Глава 3": "not_started"}
    with pytest.raises(AssembleRefused) as refused:
        assemble(project, [], doc=DOC)
    assert refused.value.code == "ASSEMBLE_SECTIONS_MISSING"


def test_a_run_that_cannot_be_read_is_listed_with_its_code_and_stops_the_assembly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.assemble_sections import AssembleRefused, assemble, where

    project = _project(tmp_path, _doc(25, 25, 20))
    scan = _scan(project, monkeypatch, [f"{DOC}#1. Глава 1"])
    Path(scan["run_root"], "run-manifest.json").write_text("{", encoding="utf-8")  # a damaged run
    found = where(project, DOC)
    assert found["unreadable"] == [{"run_id": Path(scan["run_root"]).name, "code": "ASSEMBLE_RUN"}] and found["complete"] is False
    assert {row["state"] for row in found["sections"]} == {"not_started"}
    with pytest.raises(AssembleRefused) as refused:
        assemble(project, [], doc=DOC)
    assert refused.value.code == "ASSEMBLE_RUN_UNREADABLE" and Path(scan["run_root"]).name in str(refused.value)


def test_the_output_of_the_command_holds_only_paths_headings_numbers_codes_and_ids(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state
    from tools.assemble_sections import assemble

    first, second = _closed_run(tmp_path, capsys), _closed_run(tmp_path, capsys)
    document = pilot_state.read_effective_canonical(first, pilot_state.derive_state(first)["attempts"][-1]["attempt_id"])["document"]
    result = assemble(tmp_path, [first.name, second.name])
    text = json.dumps(result, ensure_ascii=False) + Path(result["summary"]).read_text(encoding="utf-8")
    texts = [case["title"] for case in document["test_cases"]] + [row["text"] for row in document["source_requirements"]]
    assert not any(item in text for item in texts)
    summary = json.loads(Path(result["summary"]).read_text(encoding="utf-8"))
    assert set(summary) == {"schema", "document", "csv_sha256", "cases", "title_repeats", "runs", "template"}
    assert all(set(row) <= {"run_id", "docs", "csv", "code", "cases", "title_repeats", "outcome", "generated", "accepted", "canaries"} for row in summary["runs"])


def test_a_run_list_without_write_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.assemble_sections import main

    assert main(["--project", str(tmp_path), "--run", "0" * 32]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ASSEMBLE_ARGUMENTS"
