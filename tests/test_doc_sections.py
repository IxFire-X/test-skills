"""FaT-P3: ``--docs путь#Раздел`` — the inventory and the marker get one section of a Markdown file, with the line numbers
of the source file; no such heading is TASK_DOC_SECTION_MISSING with the headings of the file."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_first_run import _python_project
from tests.test_scan_guard import DOCS, _scan
from tools import doc_sections

SPEC = (
    "# Аналитика\n"                   # 1
    "\n"                              # 2
    "вводный текст\n"                 # 3
    "\n"                              # 4
    "## Функциональные требования\n"  # 5
    "\n"                              # 6
    "REQ-1 Оплата картой.\n"          # 7
    "\n"                              # 8
    "```\n"                           # 9
    "## не заголовок, а код\n"        # 10
    "```\n"                           # 11
    "\n"                              # 12
    "### Возвраты\n"                  # 13
    "\n"                              # 14
    "REQ-2 Возврат средств.\n"        # 15
    "\n"                              # 16
    "## Нефункциональные требования\n"  # 17
    "\n"                              # 18
    "REQ-3 Ответ быстрее секунды.\n"  # 19
)
FUNCTIONAL = "Функциональные требования"


def test_a_section_runs_to_the_next_heading_of_the_same_or_a_higher_level() -> None:
    section = doc_sections.find_section(SPEC, FUNCTIONAL, "docs/a.md")
    assert (section.first, section.last, section.level) == (5, 16, 2)  # the ### subsection and the fenced "##" stay inside
    nested = doc_sections.find_section(SPEC, "Возвраты")
    assert (nested.first, nested.last) == (13, 16)
    top = doc_sections.find_section(SPEC, "Аналитика")
    assert (top.first, top.last) == (1, 19)


def test_the_view_keeps_the_source_line_numbers_and_empties_the_rest() -> None:
    section = doc_sections.find_section(SPEC, FUNCTIONAL)
    lines = doc_sections.view(SPEC, section, "docs/a.md").splitlines()
    source = SPEC.splitlines()
    assert len(lines) == len(source)
    assert all(lines[number - 1] == source[number - 1] for number in range(5, 17))  # the section line for line, same numbers
    assert lines[0].startswith("<!-- раздел «Функциональные требования» файла docs/a.md, строки 5–16")
    assert all(line == "" for line in lines[1:4]) and all(line == "" for line in lines[16:])
    assert "REQ-3" not in "".join(lines) and "вводный" not in "".join(lines)
    plain = doc_sections.view(SPEC, section, "docs/a.md", note=False).splitlines()
    assert plain[0] == "" and plain[4:16] == source[4:16]


def test_a_missing_heading_names_the_headings_of_the_file() -> None:
    with pytest.raises(doc_sections.SectionMissing) as stopped:
        doc_sections.find_section(SPEC, "Интеграции", "docs/a.md")
    message = str(stopped.value)
    assert stopped.value.code == "TASK_DOC_SECTION_MISSING" and message.startswith("TASK_DOC_SECTION_MISSING:")
    for title in ("Аналитика", FUNCTIONAL, "Возвраты", "Нефункциональные требования"):
        assert title in message
    assert "не заголовок" not in message  # a line inside a code fence is no heading
    assert doc_sections.find_section(SPEC, "  Функциональные   требования ").first == 5  # whitespace is not part of the title


def test_a_spec_splits_at_the_hash_unless_the_file_really_has_one(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "c#.md").write_text("x\n", encoding="utf-8")
    assert doc_sections.resolve_spec(tmp_path, "docs/a.md#Раздел  один") == ("docs/a.md", "Раздел один")
    assert doc_sections.resolve_spec(tmp_path, "docs/a.md") == ("docs/a.md", None)
    assert doc_sections.resolve_spec(tmp_path, "docs/c#.md") == ("docs/c#.md", None)


def _project_with_sections(tmp_path: Path) -> Path:
    project = _python_project(tmp_path, runtime=False)
    (project / DOCS).write_text(SPEC, encoding="utf-8")
    return project


def test_the_entry_of_a_section_holds_only_the_section_and_binds_it(tmp_path: Path) -> None:
    from tools.build_context import build_context
    from tools.run_pipeline import _docs_entries, _docs_projection

    project = _project_with_sections(tmp_path)
    whole, = _docs_entries(project, [DOCS])
    part, = _docs_entries(project, [f"{DOCS}#{FUNCTIONAL}"])
    assert part["path"] == whole["path"] == DOCS and part["section"] == FUNCTIONAL and part["lines"] == "5-16"
    assert part["sha256"] == "sha256:" + hashlib.sha256(part["content"].encode("utf-8")).hexdigest() != whole["sha256"]
    assert "REQ-3" not in part["content"] and "REQ-2" in part["content"]
    assert _docs_projection([part]) != _docs_projection([whole]) and _docs_projection([part])[0]["section"] == FUNCTIONAL
    # the code's inventory of the documents: only the section
    texts = [row["text"] for row in build_context(project, docs_snapshot=[part])["artifacts"]["analytics_documentation"]["requirements"]]
    assert any("REQ-1" in text for text in texts) and any("REQ-2" in text for text in texts)
    assert not any("REQ-3" in text or "вводный" in text for text in texts)


def test_a_file_with_two_different_sections_is_refused(tmp_path: Path) -> None:
    from tools.run_pipeline import HostStop, _docs_entries

    project = _project_with_sections(tmp_path)
    with pytest.raises(HostStop) as stopped:
        _docs_entries(project, [f"{DOCS}#{FUNCTIONAL}", f"{DOCS}#Нефункциональные требования"])
    assert stopped.value.code == "TASK_DOC_SECTION_CONFLICT"
    assert len(_docs_entries(project, [f"{DOCS}#{FUNCTIONAL}", f"{DOCS}#{FUNCTIONAL}"])) == 1


def test_a_missing_section_stops_the_scan_before_a_run_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_sections(tmp_path)
    code, payload = _scan(project, monkeypatch, docs=[f"{DOCS}#Интеграции"])
    assert code == 2 and payload["reason"] == "TASK_DOC_SECTION_MISSING", payload
    assert FUNCTIONAL in payload["detail"] and "fix" in payload
    assert not (project / ".pilot-runs").exists() or not any((project / ".pilot-runs").iterdir())


def test_the_scan_gives_the_model_the_view_and_keeps_the_file_out_of_the_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state, step_size

    project = _project_with_sections(tmp_path)
    code, whole = _scan(project, monkeypatch)
    assert code == 0, whole
    whole_digest = json.loads((Path(whole["run_root"]) / "baselines" / next((Path(whole["run_root"]) / "baselines").iterdir()).name)
                              .read_text(encoding="utf-8"))["requirements"]["digest"]
    code, scan = _scan(project, monkeypatch, docs=[f"{DOCS}#{FUNCTIONAL}"], new_run=True)
    assert code == 0, scan
    run_root = Path(scan["run_root"])
    rows = scan["context_summary"]["document_sections"]
    assert [(row["path"], row["section"], row["lines"]) for row in rows] == [(DOCS, FUNCTIONAL, "5-16")]
    view = project / rows[0]["view"]
    assert view.is_file() and view.relative_to(run_root).parts[0] == "doc-sections"
    assert view.read_text(encoding="utf-8").splitlines()[6] == "REQ-1 Оплата картой."  # line 7 of the source file
    # the requirements step reads the view, not the file; no context receipt holds the file
    assert step_size.journal_files(project, run_root.name, "context-marker:requirements", None) == [view]
    state = pilot_state.derive_state(run_root)
    attempt_id = state["attempts"][-1]["attempt_id"]
    receipts = [pilot_state.read_context_selection(run_root, attempt_id, str(event["artifact_digest"]))
                for event in state["events"] if event["event_type"] == "CONTEXT_SELECTED"]
    assert DOCS not in [item["project_path"] for receipt in receipts for item in receipt["files"]]
    baseline = json.loads(next((run_root / "baselines").iterdir()).read_text(encoding="utf-8"))
    assert baseline["requirements"]["digest"] != whole_digest  # the requirement digest binds the section
    manifest = json.loads((run_root / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["scan_request"]["docs"] == [f"{DOCS}#{FUNCTIONAL}"]


def test_a_whole_file_scan_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_sections(tmp_path)
    code, scan = _scan(project, monkeypatch)
    assert code == 0 and scan["context_summary"]["document_sections"] == []
    run_root = Path(scan["run_root"])
    from tools import step_size

    assert step_size.journal_files(project, run_root.name, "context-marker:requirements", None) == [project / DOCS]


def test_ready_checks_the_section_of_a_doc(tmp_path: Path) -> None:
    from tools.ready import check_docs

    project = _project_with_sections(tmp_path)
    ok, = check_docs(project, [f"{DOCS}#{FUNCTIONAL}"])
    assert ok["level"] == "OK" and "5–16" in ok["message"]
    missing, = check_docs(project, [f"{DOCS}#Интеграции"])
    assert missing["level"] == "STOP" and FUNCTIONAL in missing["message"]
