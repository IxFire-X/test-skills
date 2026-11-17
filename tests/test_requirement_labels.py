"""tails13: a plain document whose lines carry numbers or labels (``D01.``, ``1.2.3.``) is not accepted as a marker of far fewer requirements.

The marker step by its public commands (``scan``, ``prepare``, ``submit``) on a project of one plain document."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_openspec_selection import _scan
from tests.test_task_docs_context import _write
from tools import api

DOC = "docs/requirements.md"
LETTERS = "# Требования\n\n## Данные\n\n" + "".join(f"D{n:02d}. Поле {n} обязательно.\n\n" for n in range(1, 13)) + "## Ошибки\n\n" + "".join(f"E{n:02d}. Ошибка {n}.\n\n" for n in range(1, 4))
HYPHENS = "# Требования\n\n" + "".join(f"- FR-{n:02d}. Требование {n}.\n" for n in range(1, 11))
NUMBERED = ("# Требования\n\n## 1. Данные\n\n### 1.1. Поля\n\n" + "".join(f"1.1.{n}. Поле {n} обязательно.\n\n" for n in range(1, 10))
            + "## 2. Ошибки\n\n" + "".join(f"2.{n}. Ошибка {n}.\n\n" for n in range(1, 4)))


def _rows(count: int) -> list[dict]:
    return [{"source_requirement_id": f"SREQ-{n:04d}", "display_order": n, "text": f"Требование {n}", "provenance": [f"{DOC}:{n}"], "digest": "sha256:" + "0" * 64}
            for n in range(1, count + 1)]


def _marker(project: Path, scan: dict, count: int, capsys: pytest.CaptureFixture[str], stage: str = "context-marker:baseline", *flags: str) -> tuple[int, dict]:
    import hashlib

    run_root = str(scan["run_root"])
    assert api.main(["prepare", "--run-root", run_root, "--stage", stage]) == 0
    slot = Path(json.loads(capsys.readouterr().out)["result"]["answer_slot"])
    digest = "sha256:" + hashlib.sha256((project / DOC).read_bytes()).hexdigest()
    rows = [{**row, "digest": digest} for row in _rows(count)]
    slot.write_text(json.dumps({"schema_version": "5.0.0", "stage": "context-marker", "warnings": [],
                                "artifacts": {"analytics_documentation": {"requirements": rows}, "source_code_and_diff": {"sources": ["(none) — no authorized source files"]}}}), encoding="utf-8")
    code = api.main(["submit", "--run-root", run_root, "--stage", stage, *flags])
    return code, json.loads(capsys.readouterr().out)


def _project(tmp_path: Path, text: str, *, big: bool = False) -> Path:
    project = tmp_path / "project"
    _write(project, DOC, text)
    for index in range(4 if big else 0):  # more context than one batch holds (with the small budget of the test): the marker goes in parts
        _write(project, f"openspec/specs/a{index}/spec.md", "## Requirements\n### Requirement: R\nText.\n#### Scenario: S\n- WHEN a\n- THEN b\n" + "x " * 1500)
    return project


@pytest.mark.parametrize("text, labels", [(LETTERS, "15"), (NUMBERED, "12"), (HYPHENS, "10")])
@pytest.mark.parametrize("flags", [(), ("--dry-run",)])
def test_far_fewer_requirements_than_labels_of_a_plain_document_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str, labels: str, flags: tuple) -> None:
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 3, capsys, "context-marker:baseline", *flags)  # the sections of the document, not its numbers
    text_out = json.dumps(out, ensure_ascii=False)
    assert code == 2 and "CONTEXT_MARKER_REQUIREMENT_LABELS" in text_out, out
    assert f"меток {labels}" in text_out and "требований 3" in text_out and DOC in text_out and "Поле 1 обязательно" not in text_out


def test_the_same_refusal_stands_at_the_requirements_step_of_a_marker_in_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import run_pipeline

    monkeypatch.setattr(run_pipeline, "_SCAN_CONTEXT_BYTE_BUDGET", 4000)
    project = _project(tmp_path, LETTERS, big=True)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 3, capsys, "context-marker:requirements")
    assert code == 2 and "CONTEXT_MARKER_REQUIREMENT_LABELS" in json.dumps(out, ensure_ascii=False), out


@pytest.mark.parametrize("text", [LETTERS, NUMBERED, HYPHENS])
def test_a_requirement_per_label_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str) -> None:
    project = _project(tmp_path, text)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, {LETTERS: 15, NUMBERED: 12, HYPHENS: 10}[text], capsys)
    assert code == 0, out


def test_a_document_without_enough_labels_or_a_single_level_list_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, "# Требования\n\n" + "".join(f"{n}. Пункт {n}.\n" for n in range(1, 20)) + "\nD01. Одно.\nD02. Два.\n")
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 2, capsys)
    assert code == 0, out


def test_a_project_with_an_id_pattern_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import build_context

    monkeypatch.setattr(build_context, "_project_id_pattern", lambda _project: r"[DE]\d{2}\.")
    project = _project(tmp_path, LETTERS)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 3, capsys)
    assert code == 0, out


def test_the_labels_are_checked_at_the_write_of_a_marker_not_at_its_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state

    project = _project(tmp_path, LETTERS)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 15, capsys)
    assert code == 0, out
    monkeypatch.setattr(pilot_state, "_check_requirement_labels", lambda *args: (_ for _ in ()).throw(AssertionError("the read of a written marker checks the labels")))
    read = pilot_state.read_model_stage_artifact(Path(scan["run_root"]), scan["attempt_id"], "context-marker:baseline", out["result"]["content_digest"])  # as the next stage reads it
    assert len(read["artifact"]["artifacts"]["analytics_documentation"]["requirements"]) == 15


def test_labels_are_forms_of_a_numbered_statement_and_nothing_else() -> None:
    from tools.build_context import document_labels

    text = "D01. a\nU12) b\nFR3: c\n- FR-01 d\n  REQ_12. e\nТР-3: f\n1.2.3. g\n2.10 h\nREQ-1001 i\nBR-3.1 j\nFR-12.4. k\n"
    assert document_labels(text) == ["D01", "U12", "FR3", "FR-01", "REQ_12", "ТР-3", "1.2.3", "2.10", "REQ-1001", "BR-3.1", "FR-12.4"]
    assert document_labels("# 1.2 Раздел\n1. список\n17.11.2026 дата\nОбычный текст D01. в середине\n") == []


def _snapshot(path: str, text: str) -> list[dict[str, str]]:
    import hashlib

    return [{"path": path, "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), "content": text}]


def test_build_context_gives_the_sections_of_a_plain_document_in_the_order_of_its_lines_with_their_line() -> None:
    from tools.build_context import build_context

    text = "## Ярлыки\n\nпервый\n\n## Данные\n\nвторой\n\n## Алфавит\n\nтретий\n"  # not in the order of the alphabet
    rows = build_context(Path("."), docs_snapshot=_snapshot(DOC, text))["artifacts"]["analytics_documentation"]["requirements"]
    assert [row["text"].splitlines()[0] for row in rows] == ["## Ярлыки", "## Данные", "## Алфавит"]
    assert [row["provenance"][0] for row in rows] == [f"{DOC}:{line}" for line in (1, 5, 9)]
    assert [row["source_requirement_id"] for row in rows] == ["SREQ-0001", "SREQ-0002", "SREQ-0003"]


def test_the_output_for_openspec_is_byte_for_byte_what_it_was() -> None:
    import hashlib

    from tests.test_openspec_selection import ADD, ADDED, BASE, SPEC
    from tools.build_context import build_context

    out = build_context(Path("."), docs_snapshot=_snapshot(BASE, SPEC) + _snapshot(ADD, ADDED))
    assert hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == "8d576f69a2cdbc5f050b39a9c332087a69ded7b60cad86e17babf10097fb0c6b"  # taken before tails13


def test_fewer_requirements_than_labels_but_not_fewer_than_half_is_a_warning_in_the_submit_and_in_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import build_report, render_markdown

    project = _project(tmp_path, NUMBERED)  # 12 numbers
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 9, capsys)
    assert code == 0 and "номеров 12, требований в разметке 9" in out["result"]["requirement_labels"] and DOC in out["result"]["requirement_labels"], out
    report, _warnings = build_report(project, scan["run_id"])
    assert report["requirement_labels"] == {"documents": [{"path": DOC, "labels": 12}], "requirements": 9} and "REQUIREMENTS_FEWER_THAN_LABELS" in report["warnings"]
    text = render_markdown(report)
    assert "REQUIREMENTS_FEWER_THAN_LABELS" in text and f"{DOC}: номеров 12" in text and "требований в разметке: 9" in text and "Поле 1 обязательно" not in text


def test_a_requirement_per_number_gives_neither_a_warning_nor_a_key_in_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import build_report, render_markdown

    project = _project(tmp_path, NUMBERED)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 12, capsys)
    assert code == 0 and "requirement_labels" not in out["result"], out
    report, _warnings = build_report(project, scan["run_id"])
    assert "requirement_labels" not in report and "REQUIREMENTS_FEWER_THAN_LABELS" not in report["warnings"] and "REQUIREMENTS_FEWER_THAN_LABELS" not in render_markdown(report)


def test_the_warning_is_in_the_submit_of_the_requirements_step_of_a_marker_in_parts_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import run_pipeline

    monkeypatch.setattr(run_pipeline, "_SCAN_CONTEXT_BYTE_BUDGET", 4000)
    project = _project(tmp_path, NUMBERED, big=True)
    scan = _scan(project, monkeypatch, [DOC])
    code, out = _marker(project, scan, 9, capsys, "context-marker:requirements")
    assert code == 0 and "номеров 12, требований в разметке 9" in out["result"]["requirement_labels"], out


def test_the_block_of_the_outcome_names_the_numbers_of_the_documents_and_the_requirements() -> None:
    from tests.test_partial_report import _acceptance
    from tools.run_report import _acceptance_lines

    report = {"acceptance": _acceptance(), "review_canaries": None, "reviewer_isolation_state": "verified", "quarantine": [], "counts": {"automation": False},
              "requirement_labels": {"documents": [{"path": DOC, "labels": 12}], "requirements": 9}}
    assert f"> **Внимание: в документе {DOC} номеров 12, требований в разметке 9.** Сверьте, что ни одно требование не пропущено и не склеено." in _acceptance_lines(report)
    assert not any("номеров" in line for line in _acceptance_lines({**report, "requirement_labels": None}))
