"""Analyst report (wave 2, Q; W2-Р7): every gap once, joined duplicates, deterministic bytes."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from tests.live_step5 import outputs
from tools import analyst_report

MARKER = outputs("9340016c")["context-marker"]


def _items(marker=MARKER, parts=(), triage=()):
    return [*analyst_report.marker_items(marker), *analyst_report.review_items(parts), *analyst_report.triage_items(triage)]


def _finding(question: str, related: list[str], code: str = "REQUIREMENT_SILENT") -> dict:
    return {"severity": "WARNING", "code": code, "related_ids": related, "message": "…", "analyst_question": question}


def test_live_warnings_become_one_item_each() -> None:
    gaps = [analyst_report.parse_warning(line) for line in MARKER["warnings"]]
    assert all(gap is not None for gap in gaps)
    assert gaps[2]["requirement_ids"] == ["SREQ-0006", "SREQ-0009"] and "нечислового сегмента пути" in gaps[2]["missing"]
    assert gaps[1]["requirement_ids"] == ["SREQ-0008"] and "синтаксически неверного JSON" in gaps[1]["missing"]
    assert gaps[4]["question"].startswith("считается ли расхождение")  # no "недостающее", a question still makes a gap
    report = analyst_report.build_report(_items(), run_id="r" * 32, attempt_id="a" * 32)
    assert len(report["items"]) == 6
    texts = [row["missing"] or "" for row in report["items"]]
    assert sum("нечислового сегмента пути" in text for text in texts) == 1  # non-numeric id
    assert sum("синтаксически неверного JSON" in text for text in texts) == 1  # broken JSON
    assert analyst_report.parse_warning("docs/x.md — redacted 2 values") is None  # not a requirement gap


def test_duplicates_from_other_sources_join_one_item() -> None:
    parts = [{"review_key": "canonical", "part_id": "part-000001",
              "result": {"findings": [_finding("Ожидается ли 400 со стандартным телом Spring или своё сообщение?", ["CREQ-B1-003", "TC-B1-003"]),
                                      {"severity": "INFO", "code": "X", "related_ids": ["TC-B1-001"], "message": "без вопроса"}]}},
             {"review_key": "r1", "part_id": "part-000001", "result": {"findings": [_finding("Нужен ли заголовок Content-Type у ответа DELETE", ["SREQ-0010"])]}}]
    triage = [{"group_id": "MUT-0006", "decision": "SPEC_GAP", "question": "Нужно ли обрезать имя длиннее 40 символов?", "requirement_id": "CREQ-B1-006",
               "refs": ["MUT-0006:L62"], "rationale": "…"},
              {"group_id": "MUT-0001", "decision": "EQUIVALENT", "refs": ["MUT-0001:L16"], "rationale": "…"}]
    marker = copy.deepcopy(MARKER)
    marker["requirement_gaps"] = [{"requirement": "SREQ-0008", "location": "docs/requirements/student-controller.md — 3.5",
                                   "missing": "ожидаемый результат для неверного тела", "blocks": "негативные кейсы POST",
                                   "question": "Какой статус и тело ошибки ожидаются для неверного тела запроса?"}]
    sources_of = {"CREQ-B1-003": ["SREQ-0006", "SREQ-0009"], "CREQ-B1-006": ["SREQ-0008"]}
    report = analyst_report.build_report(_items(marker, parts, triage), run_id="r" * 32, attempt_id="a" * 32, sources_of=sources_of)
    rows = {row["question"]: row for row in report["items"]}
    assert len(report["items"]) == 6 + 2  # the reviewer duplicate and the structured duplicate joined; one new reviewer question, one SPEC_GAP
    path_id = next(row for row in report["items"] if "нечислового сегмента пути" in (row["missing"] or ""))
    assert [source["kind"] for source in path_id["sources"]] == ["context-marker", "tc-reviewer"]
    body = next(row for row in report["items"] if row["requirement_ids"] == ["SREQ-0008"] and "статус и тело ошибки" in row["question"])
    assert [source["ref"] for source in body["sources"]] == ["requirement_gaps[0]", "warnings[1]"]
    spec = rows["Нужно ли обрезать имя длиннее 40 символов?"]
    assert spec["requirement_ids"] == ["SREQ-0008"] and spec["sources"] == [{"kind": "mutation-triage", "ref": "MUT-0006", "location": "MUT-0006:L62"}]
    assert report["counts"] == {"context-marker": 6, "tc-reviewer": 1, "autotest-reviewer": 1, "mutation-triage": 1}


def test_the_report_is_deterministic() -> None:
    parts = [{"review_key": "canonical", "part_id": f"part-00000{n}", "result": {"findings": [_finding(f"Вопрос {n}?", [f"SREQ-000{n}"])]}} for n in range(1, 4)]
    items = _items(MARKER, parts)
    first = analyst_report.build_report(items, run_id="r" * 32, attempt_id="a" * 32)
    for seed in range(5):
        shuffled = list(items)
        random.Random(seed).shuffle(shuffled)
        again = analyst_report.build_report(shuffled, run_id="r" * 32, attempt_id="a" * 32)
        assert json.dumps(again, ensure_ascii=False, sort_keys=True) == json.dumps(first, ensure_ascii=False, sort_keys=True)
        assert analyst_report.render_markdown(again) == analyst_report.render_markdown(first)
    assert len({row["item_id"] for row in first["items"]}) == len(first["items"])
    comment = analyst_report.openspec_comment(first, "add-students")
    assert comment.count("- [ ] ") == len(first["items"]) and "add-students" in comment


def test_new_answer_fields_are_optional_in_their_schemas() -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    marker = {**copy.deepcopy(MARKER), "schema_version": "5.1.0", "requirement_gaps": [
        {"requirement": "SREQ-0008", "location": "x — 3.5", "missing": "m", "blocks": "b", "question": "q"}]}
    assert schema_diagnostics(marker, root / "schemas" / "context-marker-output.schema.json", root) == []
    assert schema_diagnostics(MARKER, root / "schemas" / "context-marker-output.schema.json", root) == []  # 5.0.0 answers stay valid
    from tools.review_modes import output_version

    assert output_version("compact-v1", {"findings": [_finding("q?", ["SREQ-0001"])]}) == "2.1.0"
    assert output_version("compact-v1", {"findings": [{"severity": "INFO", "code": "X", "related_ids": ["A"], "message": "m"}]}) == "2.0.0"
    assert output_version("pairs", {"findings": [_finding("q?", ["SREQ-0001"])]}) == "1.0.0"


@pytest.mark.parametrize("option", [False, True])
def test_the_driver_writes_the_report_only_with_the_option(tmp_path: Path, option: bool) -> None:
    from tests.test_review_fixes_driver import SavedModel, _drive, _project, _start

    project = _project(tmp_path, local=False)
    done = _drive(project, _start(project, "cases-only-v1", **({"analyst_report": True} if option else {})), SavedModel("cases-only-v1"))
    result = done["result"]
    if not option:
        assert "analyst_questions" not in result and "analyst_report_json" not in result["paths"] and result["schema_version"] == "1.0.0"
        return
    report = json.loads(Path(result["paths"]["analyst_report_json"]).read_text(encoding="utf-8"))
    assert result["analyst_questions"] == len(report["items"]) and Path(result["paths"]["analyst_report_markdown"]).is_file()
    from tools.pipeline_driver_analyst import attempt_report

    assert attempt_report(project, done["run_id"]) == report  # rebuilt from durable sources: same report
