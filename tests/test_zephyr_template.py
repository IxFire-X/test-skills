"""FaT-7: a Zephyr import CSV filled from the project's own export template, beside the bundle and outside its receipt."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture

TEMPLATE_HEADER = ["Key", "Name", "АС", "Автоматизирован", "Вид тестирования", "Команда", "Приоритет теста", "Статус", "Story Points"]
TEMPLATE_ROW = ["", "Заглушка", "АС-4242", "Да", "Функциональное", "Команда Альфа", "High", "Черновик", "3"]
SECRET_VALUES = ("АС-4242", "Функциональное", "Команда Альфа", "Черновик")


def _template_bytes(delimiter: str = ",", bom: bool = True, row: list[str] | None = None, encoding: str = "utf-8") -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(TEMPLATE_HEADER)
    writer.writerow(["", "", "", "", "", "", "", "", ""])
    writer.writerow(row or TEMPLATE_ROW)
    text = out.getvalue()
    return (b"\xef\xbb\xbf" if bom and encoding == "utf-8" else b"") + text.encode(encoding)


def _published(tmp_path: Path) -> tuple[dict, Path]:
    from tools.publish_test_case_bundle import publish_bundle

    document = canonical_fixture()
    receipt = publish_bundle(document, tmp_path / "cases")
    return document, Path(receipt.csv_path)


def _rows(data: bytes) -> tuple[str, list[list[str]]]:
    text = data.decode("utf-8-sig")
    delimiter = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
    return delimiter, list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


# --- reading the template -----------------------------------------------------------------------

@pytest.mark.parametrize(("delimiter", "bom"), [(",", True), (",", False), (";", True), (";", False)])
def test_the_template_is_read_with_either_delimiter_and_with_or_without_bom(tmp_path: Path, delimiter: str, bom: bool) -> None:
    from tools.zephyr_template_export import read_template

    (tmp_path / "zephyr").mkdir()
    (tmp_path / "zephyr" / "stubs.csv").write_bytes(_template_bytes(delimiter, bom))
    template = read_template(tmp_path, "zephyr/stubs.csv")
    assert template.delimiter == delimiter
    assert template.header == TEMPLATE_HEADER
    assert template.values["АС"] == "АС-4242" and template.values["Story Points"] == "3"
    assert template.digest.startswith("sha256:")


@pytest.mark.parametrize(("setup", "code"), [
    ("cp1251", "ZEPHYR_TEMPLATE_ENCODING"),
    ("missing", "ZEPHYR_TEMPLATE_MISSING"),
    ("outside", "ZEPHYR_TEMPLATE_OUTSIDE"),
    ("not_csv", "ZEPHYR_TEMPLATE_NOT_CSV"),
    ("no_columns", "ZEPHYR_TEMPLATE_COLUMNS"),
    ("empty", "ZEPHYR_TEMPLATE_EMPTY"),
    ("large", "ZEPHYR_TEMPLATE_TOO_LARGE"),
])
def test_a_bad_template_stops_with_a_clear_code_and_hint(tmp_path: Path, setup: str, code: str) -> None:
    from tools.zephyr_template_export import ZephyrTemplateError, read_template

    project = tmp_path / "project"
    project.mkdir()
    path = "stubs.csv"
    if setup == "cp1251":
        (project / path).write_bytes(_template_bytes(bom=False, encoding="cp1251"))
    elif setup == "outside":
        (tmp_path / "stubs.csv").write_bytes(_template_bytes())
        path = "../stubs.csv"
    elif setup == "not_csv":
        (project / path).write_text("просто текст без колонок\n", encoding="utf-8")
    elif setup == "no_columns":
        (project / path).write_text("Key,Name\r\n,Заглушка\r\n", encoding="utf-8")
    elif setup == "empty":
        (project / path).write_text(",".join(TEMPLATE_HEADER) + "\r\n", encoding="utf-8")
    elif setup == "large":
        (project / path).write_bytes(_template_bytes() + b"," * (1024 * 1024))
    with pytest.raises(ZephyrTemplateError) as caught:
        read_template(project, path)
    assert caught.value.code == code and caught.value.hint
    if setup == "cp1251":
        assert "сохраните шаблон в UTF-8" in caught.value.hint


# --- building the import CSV --------------------------------------------------------------------

def test_custom_fields_are_filled_and_extra_columns_appended(tmp_path: Path) -> None:
    from tools.zephyr_template_export import build_import_csv, read_template

    document, bundle_csv = _published(tmp_path)
    (tmp_path / "stubs.csv").write_bytes(_template_bytes())
    data = build_import_csv(bundle_csv.read_bytes(), document, read_template(tmp_path, "stubs.csv"), automated_case_ids=set())
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data
    _delimiter, rows = _rows(data)
    _ours, our_rows = _rows(bundle_csv.read_bytes())
    header = rows[0]
    assert header[: len(our_rows[0])] == our_rows[0] and header[len(our_rows[0]):] == ["Story Points"]
    first = dict(zip(header, rows[1]))
    assert first["АС"] == "АС-4242" and first["Вид тестирования"] == "Функциональное" and first["Команда"] == "Команда Альфа"
    assert first["Приоритет теста"] == "High" and first["Статус"] == "Черновик" and first["Story Points"] == "3"
    for row in rows[2:]:
        step = dict(zip(header, row))
        assert step["Name"] == "" and step["АС"] == "" and step["Story Points"] == ""
    assert [row[: len(our_rows[0])] for row in rows[1:]] == [
        [value if column not in TEMPLATE_HEADER[2:8] else dict(zip(header, row))[column] for column, value in zip(our_rows[0], our)]
        for row, our in zip(rows[1:], our_rows[1:])
    ]


def test_the_template_delimiter_is_kept_in_the_output(tmp_path: Path) -> None:
    from tools.zephyr_template_export import build_import_csv, read_template

    document, bundle_csv = _published(tmp_path)
    (tmp_path / "stubs.csv").write_bytes(_template_bytes(";", bom=False))
    data = build_import_csv(bundle_csv.read_bytes(), document, read_template(tmp_path, "stubs.csv"), automated_case_ids=set())
    delimiter, rows = _rows(data)
    assert delimiter == ";" and dict(zip(rows[0], rows[1]))["АС"] == "АС-4242"


@pytest.mark.parametrize(("template_value", "automated", "expected"), [
    ("Да", set(), "Нет"),
    ("Да", {"TC-batch-a-001"}, "Да"),
    ("нет", {"TC-batch-a-001"}, "да"),
    ("true", set(), "false"),
    ("Частично", {"TC-batch-a-001"}, "Частично"),
])
def test_automated_is_computed_in_the_template_form(tmp_path: Path, template_value: str, automated: set, expected: str) -> None:
    from tools.zephyr_template_export import build_import_csv, read_template

    document, bundle_csv = _published(tmp_path)
    row = list(TEMPLATE_ROW)
    row[3] = template_value
    (tmp_path / "stubs.csv").write_bytes(_template_bytes(row=row))
    data = build_import_csv(bundle_csv.read_bytes(), document, read_template(tmp_path, "stubs.csv"), automated_case_ids=automated)
    _delimiter, rows = _rows(data)
    assert dict(zip(rows[0], rows[1]))["Автоматизирован"] == expected


def test_a_case_with_blockers_is_never_automated(tmp_path: Path) -> None:
    from tools.zephyr_template_export import build_import_csv, read_template

    document, bundle_csv = _published(tmp_path)
    document["test_cases"][0]["automation_blockers"] = [{"code": "X"}]
    (tmp_path / "stubs.csv").write_bytes(_template_bytes())
    data = build_import_csv(bundle_csv.read_bytes(), document, read_template(tmp_path, "stubs.csv"), automated_case_ids={"TC-batch-a-001"})
    _delimiter, rows = _rows(data)
    assert dict(zip(rows[0], rows[1]))["Автоматизирован"] == "Нет"


# --- the command ----------------------------------------------------------------------------------

def _template_in(project: Path) -> None:
    (project / "zephyr").mkdir(exist_ok=True)
    (project / "zephyr" / "stubs.csv").write_bytes(_template_bytes())
    skillsrc = json.loads(Path("tests/fixtures/projects/python-pytest/.skillsrc").read_text(encoding="utf-8"))
    skillsrc["projection"] = {"zephyr": {"template": "zephyr/stubs.csv"}}
    (project / ".skillsrc").write_text(json.dumps(skillsrc), encoding="utf-8")


def _cases_only_run(tmp_path: Path, capsys: pytest.CaptureFixture[str], *, close: bool = True) -> tuple[Path, Path, Path]:
    """A real cases-only run whose effective set is published to ``cases``; closed by ``finish`` when asked."""
    from tests.test_finish import _cases_only_effective, _finish
    from tools import pilot_state
    from tools.publish_test_case_bundle import publish_bundle

    run_root, attempt_id = _cases_only_effective(tmp_path)
    project = run_root.parents[1]
    document = pilot_state.read_effective_canonical(run_root, attempt_id)["document"]
    bundle_csv = Path(publish_bundle(document, project / "cases").csv_path)
    if close:
        _finish(project, run_root, capsys)
    _template_in(project)
    return project, run_root, bundle_csv


def test_the_command_writes_the_import_file_and_records_only_path_and_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.zephyr_template_export import _automation_facts, export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    assert _automation_facts(run_root) == (True, set())
    bundle_before = bundle_csv.read_bytes()
    result = export_import_csv(project, run_root.name, bundle_csv)
    target = Path(result["output"])
    assert target.name.endswith(".zephyr-import.csv") and target.parent == bundle_csv.parent
    assert result["template_path"] == "zephyr/stubs.csv" and result["template_digest"].startswith("sha256:")
    assert result["columns_added"] == ["Story Points"] and result["rows"] >= 1
    assert bundle_csv.read_bytes() == bundle_before
    _delimiter, rows = _rows(target.read_bytes())
    assert dict(zip(rows[0], rows[1]))["Автоматизирован"] == "Нет"  # cases-only: no accepted automation
    record = (run_root / "zephyr-import.json").read_bytes()
    for value in SECRET_VALUES:
        assert value.encode("utf-8") not in record and value not in json.dumps(result, ensure_ascii=False)
    assert export_import_csv(project, run_root.name, bundle_csv) == result


def test_different_bytes_in_an_existing_import_file_are_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.zephyr_template_export import ZephyrTemplateError, export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    target = Path(export_import_csv(project, run_root.name, bundle_csv)["output"])
    target.write_bytes(b"changed")
    with pytest.raises(ZephyrTemplateError) as caught:
        export_import_csv(project, run_root.name, bundle_csv)
    assert caught.value.code == "ZEPHYR_EXPORT_CONFLICT" and target.read_bytes() == b"changed"


def test_a_link_at_the_import_path_is_a_conflict_even_to_the_same_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FaT-WT: the ``is_symlink`` branch of ``_install`` — a link to a file with the very bytes of the export is refused."""
    import os

    from tools.zephyr_template_export import ZephyrTemplateError, export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    target = Path(export_import_csv(project, run_root.name, bundle_csv)["output"])
    data = target.read_bytes()
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_bytes(data)
    target.unlink()
    try:
        os.symlink(elsewhere, target)
    except OSError as error:  # pragma: no cover - a Windows host without the symlink privilege
        pytest.skip(f"no symlink here: {error}")
    with pytest.raises(ZephyrTemplateError) as caught:
        export_import_csv(project, run_root.name, bundle_csv)
    assert caught.value.code == "ZEPHYR_EXPORT_CONFLICT" and target.is_symlink() and elsewhere.read_bytes() == data


def test_without_a_template_nothing_is_written_and_the_bundle_is_unchanged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.zephyr_template_export import export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    (project / ".skillsrc").unlink()
    before = sorted(path.name for path in bundle_csv.parent.iterdir())
    assert export_import_csv(project, run_root.name, bundle_csv) == {"status": "skipped", "reason": "NO_TEMPLATE"}
    assert sorted(path.name for path in bundle_csv.parent.iterdir()) == before


def test_the_export_waits_for_the_closed_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.zephyr_template_export import ZephyrTemplateError, export_import_csv

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys, close=False)
    with pytest.raises(ZephyrTemplateError) as caught:
        export_import_csv(project, run_root.name, bundle_csv)
    assert caught.value.code == "ZEPHYR_EXPORT_NOT_CLOSED"


def test_a_set_from_another_run_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.publish_test_case_bundle import publish_bundle
    from tools.zephyr_template_export import ZephyrTemplateError, export_import_csv

    project, run_root, _bundle_csv = _cases_only_run(tmp_path, capsys)
    other = Path(publish_bundle(canonical_fixture(), project / "other-run-cases").csv_path)
    with pytest.raises(ZephyrTemplateError) as caught:
        export_import_csv(project, run_root.name, other)
    assert caught.value.code == "ZEPHYR_EXPORT_RUN_MISMATCH" and caught.value.hint
    assert not list(other.parent.glob("*.zephyr-import.csv"))


def test_a_wrong_cases_name_is_a_clear_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.zephyr_template_export import main

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    capsys.readouterr()
    assert main(["--project", str(project), "--run", run_root.name, "--cases", str(bundle_csv.with_suffix(".txt"))]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "ZEPHYR_EXPORT_CASES" and out["hint"]


def test_an_accepted_pass_run_marks_its_case_automated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests import test_durable_execution_trace as trace
    from tests.test_finish import _finish
    from tools import pilot_state
    from tools.zephyr_template_export import _automation_facts, build_import_csv, read_template

    root, attempt_id, report, request, _durable = trace._execution_facts(tmp_path)
    trace._publish_execution(root, attempt_id, report, request=request)
    code, output = _finish(tmp_path / "project", root, capsys)
    assert output["verification"] == "PASS" and output["accepted"] is True, output
    closed, automated = _automation_facts(root)
    document = pilot_state.read_effective_canonical(root, attempt_id)["document"]
    case_ids = {case["case_id"] for case in document["test_cases"]}
    assert closed and automated and automated <= case_ids
    from tools.publish_test_case_bundle import publish_bundle

    bundle_csv = Path(publish_bundle(document, tmp_path / "cases").csv_path)
    (tmp_path / "stubs.csv").write_bytes(_template_bytes())
    data = build_import_csv(bundle_csv.read_bytes(), document, read_template(tmp_path, "stubs.csv"), automated_case_ids=automated)
    _delimiter, rows = _rows(data)
    header = rows[0]
    first_rows = [dict(zip(header, row)) for row in rows[1:] if dict(zip(header, row))["Name"]]
    assert any(row["Автоматизирован"] == "Да" for row in first_rows)


def test_a_skillsrc_with_the_template_still_validates(tmp_path: Path) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    document = json.loads(Path("tests/fixtures/projects/python-pytest/.skillsrc").read_text(encoding="utf-8"))
    document["projection"] = {"zephyr": {"template": "zephyr/stubs.csv"}}
    (tmp_path / ".skillsrc").write_text(json.dumps(document), encoding="utf-8")
    assert load_skillsrc(tmp_path / ".skillsrc")["projection"]["zephyr"]["template"] == "zephyr/stubs.csv"


@pytest.mark.parametrize("failure", [ValueError("'Name' is not in list"), IndexError("list index out of range"), StopIteration()])
def test_an_error_without_a_code_is_one_json_line_with_code_two(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                 monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """FaT-B: errors without .code never leave a traceback — one JSON line, code 2."""
    import tools.zephyr_template_export as export

    def broken(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(export, "export_import_csv", broken)
    assert export.main(["--project", str(tmp_path), "--run", "r", "--cases", str(tmp_path / "x.zephyr-scale.csv")]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error" and output["code"] == "ZEPHYR_EXPORT_FAILED" and output["hint"]


def test_a_copy_of_the_set_outside_the_project_is_refused_fat_b(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FaT-B: the import file is never written beside a copy outside the project."""
    import shutil

    from tools.zephyr_template_export import main

    project, run_root, bundle_csv = _cases_only_run(tmp_path, capsys)
    capsys.readouterr()
    outside = tmp_path.parent / (tmp_path.name + "-outside")  # the project is tmp_path itself
    outside.mkdir()
    copy = Path(shutil.copy2(bundle_csv, outside / bundle_csv.name))
    shutil.copy2(bundle_csv.with_name(bundle_csv.name.removesuffix(".zephyr-scale.csv") + ".json"), outside)
    assert main(["--project", str(project), "--run", run_root.name, "--cases", str(copy)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "ZEPHYR_EXPORT_CASES"
    assert not list(outside.glob("*.zephyr-import.csv"))
