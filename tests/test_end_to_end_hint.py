"""FaT-E2E: the end-to-end case hint (CV_NO_END_TO_END) and the one rule of requirement groups shared with plan-batches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fat-bo"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _document(cases: dict[str, list[str]], sources: dict[str, str]) -> dict:
    """A document reduced to what the hint reads: canonical requirement ← source requirement, case → requirements."""
    return {"source_to_canonical_mappings": [{"source_requirement_id": source, "canonical_requirement_ids": [canonical]}
                                             for canonical, source in sources.items()],
            "test_cases": [{"case_id": case_id, "requirement_ids": requirement_ids} for case_id, requirement_ids in cases.items()]}


SOURCES = {"CREQ-O1": "SREQ-1", "CREQ-P1": "SREQ-2", "CREQ-V1": "SREQ-3", "CREQ-G1": "SREQ-4"}
GROUPS = {"SREQ-1": "prefix:O", "SREQ-2": "prefix:P", "SREQ-3": "prefix:V", "SREQ-4": "section:Общие"}


def test_no_case_linking_two_groups_is_a_hint_with_counts_only() -> None:
    from tools.review_view import NO_END_TO_END, end_to_end_hint

    document = _document({"TC-1": ["CREQ-O1"], "TC-2": ["CREQ-P1"], "TC-3": ["CREQ-V1"]}, SOURCES)
    assert end_to_end_hint(document, GROUPS) == {"code": NO_END_TO_END, "groups": 4, "linking_cases": 0}


def test_one_case_through_two_groups_removes_the_hint() -> None:
    from tools.review_view import end_to_end_hint, linking_cases

    document = _document({"TC-0": ["CREQ-O1", "CREQ-P1", "CREQ-V1"], "TC-1": ["CREQ-O1"], "TC-2": ["CREQ-P1"]}, SOURCES)
    assert end_to_end_hint(document, GROUPS) is None and linking_cases(document, GROUPS) == ["TC-0"]


def test_a_constraint_group_every_case_cites_links_nothing() -> None:
    """Common constraints cited by every case (FR/NFR, «Общие») are not a step of a process."""
    from tools.review_view import end_to_end_hint, linking_cases

    document = _document({"TC-1": ["CREQ-O1", "CREQ-G1"], "TC-2": ["CREQ-P1", "CREQ-G1"], "TC-3": ["CREQ-V1", "CREQ-G1"]}, SOURCES)
    assert linking_cases(document, GROUPS) == []
    assert end_to_end_hint(document, GROUPS)["groups"] == 3


def test_one_group_or_requirements_without_groups_give_no_hint() -> None:
    from tools.review_view import end_to_end_hint

    document = _document({"TC-1": ["CREQ-O1"], "TC-2": ["CREQ-P1"]}, SOURCES)
    assert end_to_end_hint(document, dict.fromkeys(GROUPS, "prefix:O")) is None
    assert end_to_end_hint(document, dict.fromkeys(GROUPS)) is None


def test_batches_holding_one_group_each_give_no_hint_and_a_line_in_the_plan_notes() -> None:
    from tools.batch_assembly import plan_batches
    from tools.review_view import end_to_end_hint

    document = _document({"TC-B1-1": ["CREQ-O1"], "TC-B2-1": ["CREQ-P1"]}, SOURCES)
    groups = {"SREQ-1": "prefix:O", "SREQ-2": "prefix:P"}
    assert end_to_end_hint(document, groups, [["SREQ-1"], ["SREQ-2"]]) is None
    assert end_to_end_hint(document, groups, [["SREQ-1", "SREQ-2"]]) is not None  # one batch with both groups: the path fits

    requirement = lambda number, text: {"source_requirement_id": f"SREQ-{number:04d}", "display_order": number, "text": text,  # noqa: E731
                                        "provenance": [f"docs/req.md — {number}"], "digest": "sha256:" + f"{number:064x}"}
    plan = plan_batches([requirement(1, "A01. " + "а" * 3000), requirement(2, "B01. " + "б" * 3000)],
                        {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64},
                        window_bytes=200_000, input_bytes=40_000)
    assert len(plan["batches"]) == 2
    assert any(note.startswith("сквозной кейс через пачки невозможен") for note in plan.notes), plan.notes


@pytest.mark.parametrize(("project", "window", "input_bytes", "expected", "count"), [
    # canonical bytes of the plans before FaT-E2E (develop de0f96a): the shared group rule changes no batch
    ("petclinic", 1_000_000, 385_000, "df9581bde4dec75b1f0729e188aa450221ac4450acaa842617934d5d33f9c77c", 2),
    ("microservices", 600_000, 198_000, "67f0672dea6437e3714e2a26f8cd8bea13dbad63cfc0528d8b4cb7ec3bc6b72a", 3),
    ("petclinic", 0, 0, "c097a785fb666a894650ba8521be7c23042b4e39c87b3da91f0a7108211c6b72", 1),
    ("microservices", 0, 0, "0b5fe5c03ea47148d905eb296009844dcf2c878ff70a4fde4c20a56939a21488", 1),
])
def test_plan_batches_with_the_shared_group_rule_gives_the_same_batches(project, window, input_bytes, expected, count) -> None:
    from tools.batch_assembly import canonical_bytes, plan_batches

    recorded = _load(f"{project}-plan.json")
    plan = plan_batches(recorded["source_requirements"], _load(f"{project}-grouping.json"), window_bytes=window, input_bytes=input_bytes)
    assert hashlib.sha256(canonical_bytes(plan)).hexdigest() == expected and len(plan["batches"]) == count


def _project(tmp_path: Path, document: str) -> tuple[Path, dict]:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    data = document.encode("utf-8")
    (project / "docs" / "req.md").write_bytes(data)
    return project, {"files": [{"project_path": "docs/req.md", "content_digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}


def _sources(texts: list[str]) -> list[dict]:
    return [{"source_requirement_id": f"SREQ-{number}", "display_order": number, "text": text, "provenance": ["docs/req.md"],
             "digest": "sha256:" + f"{number:064x}"} for number, text in enumerate(texts, start=1)]


def test_requirement_groups_by_letter_prefix_and_by_document_section(tmp_path: Path) -> None:
    from tools.batch_assembly import requirement_groups

    texts = ["O01. Создать владельца", "P01. Добавить питомца"]
    project, receipt = _project(tmp_path, "\n".join(texts))
    assert requirement_groups(_sources(texts), project, receipt) == {"SREQ-1": "prefix:O", "SREQ-2": "prefix:P"}
    texts = ["REQ-1 Оплата заказа целиком", "REQ-2 Возврат заказа целиком"]
    project, receipt = _project(tmp_path / "s", "# Платежи\n\n## Оплата\n\n" + texts[0] + "\n\n## Возвраты\n\n" + texts[1] + "\n")
    groups = requirement_groups(_sources(texts), project, receipt)
    assert groups == {"SREQ-1": "section:docs/req.md#Оплата", "SREQ-2": "section:docs/req.md#Возвраты"}


def test_requirement_groups_by_the_skillsrc_id_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import build_context
    from tools.batch_assembly import requirement_groups

    monkeypatch.setattr(build_context, "_project_id_pattern", lambda _project: r"(?P<group>\d+)\.\d+\s")
    texts = ["3.1 Первое", "3.2 Второе", "4.1 Третье"]
    project, receipt = _project(tmp_path, "\n".join(texts))
    assert requirement_groups(_sources(texts), project, receipt) == {"SREQ-1": "id:3", "SREQ-2": "id:3", "SREQ-3": "id:4"}


def test_render_without_groups_is_byte_identical_and_with_them_shows_the_hint() -> None:
    from tests.test_review_view import _example
    from tools.review_view import NO_END_TO_END, render

    document = _example()
    assert hashlib.sha256(render(document)).hexdigest() == "df245643eca8b66e8cc49f2c7fb1756465d9f9000cf78c79b654bf5d4a2a2b82"  # de0f96a
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    alone = {row["source_requirement_id"]: f"prefix:{index}" for index, row in enumerate(document["source_requirements"])}
    case_requirements = [set(case["requirement_ids"]) for case in document["test_cases"]]
    view = render(document, "review", alone).decode("utf-8")
    hinted = NO_END_TO_END in view
    from tools.review_view import end_to_end_hint

    assert hinted is (end_to_end_hint(document, alone) is not None)
    assert render(document, "review", dict.fromkeys(alone)) == render(document)  # no groups — no hint, same bytes
    assert json.dumps(document, ensure_ascii=False, sort_keys=True) == before and case_requirements  # the case text is never changed


def test_run_report_names_the_hint_by_counts_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import review_view, run_report

    document = _document({"TC-1": ["CREQ-O1"], "TC-2": ["CREQ-P1"]}, SOURCES)
    monkeypatch.setattr(review_view, "run_requirement_groups", lambda *_args: (GROUPS, None))
    assert run_report.end_to_end(tmp_path, "a" * 32, document) == {"code": "CV_NO_END_TO_END", "groups": 4, "linking_cases": 0}
    monkeypatch.setattr(review_view, "run_requirement_groups", lambda *_args: (_ for _ in ()).throw(ValueError("no context")))
    assert run_report.end_to_end(tmp_path, "a" * 32, document) is None  # the report never stops on the hint
    assert "CV_NO_END_TO_END" in run_report._WARNING_TEXT
