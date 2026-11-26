"""«Только изменение» (v0.8, R&D-14): ``--docs`` names a change of OpenSpec and not the spec.md of its area — the spec.md is the
base of the composition, its other requirements are context; what does not apply leaves the run as a line, not a stop.
The lines for the tester are the texts the user approved on 25.11 (handoff/v08/change-only-design.md §3)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_openspec_marker import _answer_of, _prepare
from tests.test_openspec_selection import BASE, _project, _scan
from tests.test_task_docs_context import _write
from tools.build_context import RequirementConflict, build_context, openspec_applied, openspec_change_diff
from tools.openspec_scope import Weights, impact_areas, lines, neighbours, question, summary_tail

pytestmark = pytest.mark.contract

CHANGE = ["openspec/changes/add-search"]
DELTA = "openspec/changes/add-search/specs/owners/spec.md"
SCOPE = ("Объём OpenSpec: только изменение add-search; базовые спецификации областей — контекст; "
         "для полной регрессии области добавьте --docs openspec/specs/owners")
SPEC = "## Requirements\n### Requirement: Create\nCreate an owner.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n"
SEARCH = "### Requirement: Search\nFind an owner.\n#### Scenario: Ok\n- WHEN typed\n- THEN listed\n"


def _entry(path: str, text: str, **mark: bool) -> dict:
    return {"path": path, "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), "content": text, **mark}


def _composed(delta: str, spec: str = SPEC) -> tuple[list[str], list[str]]:
    """(the names of the requirements, the lines) of a change only composed with the base."""
    entries = [_entry(BASE, spec, base=True), _entry(DELTA, delta)]
    rows = build_context(Path("."), docs_snapshot=entries)["artifacts"]["analytics_documentation"]["requirements"]
    return [row["text"].splitlines()[0] for row in rows], openspec_applied(entries)[1]


def test_the_change_folder_alone_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _scan(_project(tmp_path), monkeypatch, CHANGE)["status"] == "ok"


def test_code_writes_only_the_requirements_of_the_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    scan = _scan(_project(tmp_path), monkeypatch, CHANGE)
    _prepare(Path(scan["run_root"]), "context-marker:requirements", capsys)
    rows = _answer_of(Path(scan["run_root"]), scan["attempt_id"])["artifacts"]["analytics_documentation"]["requirements"]
    assert [row["text"].splitlines()[0] for row in rows] == ["### Requirement: Search"]


def test_scan_says_the_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert SCOPE in _scan(_project(tmp_path), monkeypatch, CHANGE)["warnings"]


def test_ready_says_the_scope(tmp_path: Path) -> None:
    from tools import ready

    rows = ready.check(_project(tmp_path), CHANGE, "cases-only-v1", None)["checks"]
    assert [(row["level"], row["message"]) for row in rows if row["id"] == "openspec:scope"] == [("OK", SCOPE)]


def test_a_modified_the_base_lacks_leaves_the_run() -> None:
    names, said = _composed("## ADDED Requirements\n" + SEARCH + "## MODIFIED Requirements\n### Requirement: Creat\nCreate fast.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n")
    assert names == ["### Requirement: Search"]
    assert (f"Не применено: MODIFIED «Creat» ({DELTA}:8) — в spec.md области owners нет требования с таким именем; ближайшие: «Create»") in [
        line.split("; смежные с ним: ")[0] for line in said]


def test_an_added_with_another_text_leaves_the_run() -> None:
    _names, said = _composed("## ADDED Requirements\n" + SEARCH + "### Requirement: Create\nCreate two owners.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n")
    assert f"Не применено: ADDED «Create» ({DELTA}:7) — в spec.md то же требование с другим текстом: {BASE}:2" in said


def test_an_incomplete_scope_is_counted() -> None:
    _names, said = _composed("## ADDED Requirements\n" + SEARCH + "### Requirement: Create\nCreate two owners.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n")
    assert "Объём неполный: применено 1 из 2 требований изменения add-search; не взято 1 — причины и вопросы в questions-for-analyst.md" in said


def test_nothing_applied_stops() -> None:
    with pytest.raises(RequirementConflict, match="no requirement of the change applies"):
        _composed("## MODIFIED Requirements\n### Requirement: Gone\nx.\n#### Scenario: Ok\n- WHEN a\n- THEN b\n")


def test_a_change_that_only_removes_stops() -> None:
    with pytest.raises(RequirementConflict, match="only removes"):
        _composed("## REMOVED Requirements\n### Requirement: Create\n")


def test_a_repeated_name_still_stops() -> None:
    with pytest.raises(RequirementConflict, match="repeats a requirement"):
        _composed("## ADDED Requirements\n" + SEARCH + "## MODIFIED Requirements\n" + SEARCH)


def test_a_shared_rare_word_makes_a_neighbour() -> None:
    pool = [("visits", "Book", "Book a visit by «телефон»."), ("visits", "Cancel", "Cancel a visit.")]
    weights = Weights([text for _area, _name, text in pool] + ["Find an owner by «телефон»."])
    assert [name for _score, _area, name in neighbours(weights, "Find an owner by «телефон».", pool, set())] == ["Book"]


def test_an_area_of_impact_gives_a_neighbour_below_the_threshold() -> None:
    pool = [("vets", "List", "List the vets and their owner rating score."), ("vets", "Rate", "Rate a vet.")]
    text = "Find an owner quickly by name today."
    weights = Weights([body for _area, _name, body in pool] + [text])
    assert neighbours(weights, text, pool, set()) == []
    assert [name for _score, _area, name in neighbours(weights, text, pool, impact_areas("## Impact\n- vets\n", {"vets"}))] == ["List"]


def test_an_impact_of_windows_line_ends_is_read() -> None:
    assert impact_areas("## Impact\r\n- vets\r\n", {"vets"}) == {"vets"}


def test_scan_shows_eight_neighbours() -> None:
    pool = [("visits", f"N{index}", f"Book «{'альфа' if index < 5 else 'бета'}» {index}0") for index in range(9)]  # five near S, four near T
    changed = [("ADDED", "owners", "S", "Find «альфа»"), ("ADDED", "owners", "T", "Find «бета»")]
    [said] = [line for line in lines("c", ["owners"], changed, [], pool, "", 8) if line.startswith("Проверьте")]
    assert said.count(" (рядом с «") == 8 and " и ещё 1 — в report.md; полная регрессия области — --docs openspec/specs/visits" in said


def test_the_view_of_a_modified_has_was_and_now() -> None:
    delta = "## MODIFIED Requirements\n### Requirement: Create\nCreate an owner.\n#### Scenario: Ok\n- WHEN saved\n- THEN listed\n"
    [view] = openspec_change_diff([_entry(BASE, SPEC, base=True), _entry(DELTA, delta)])
    assert view["changed"] == [{"scenario": "Ok", "was": ["- WHEN saved", "- THEN stored"], "now": ["- WHEN saved", "- THEN listed"]}]


def test_an_added_has_no_view() -> None:
    assert openspec_change_diff([_entry(BASE, SPEC, base=True), _entry(DELTA, "## ADDED Requirements\n" + SEARCH)]) == []


def test_a_line_not_applied_is_a_question() -> None:
    line = f"Не применено: ADDED «Create» ({DELTA}:7) — в spec.md то же требование с другим текстом: {BASE}:2; смежные с ним: owners/«Fax»"
    assert question(line) == (f"- ADDED «Create» ({DELTA}:7): в spec.md то же требование с другим текстом: {BASE}:2. "
                              "Кейсы на него не написаны. Поправьте изменение или spec.md и повторите прогон.")


def test_a_path_with_brackets_is_a_question_too() -> None:
    assert question("Не применено: MODIFIED «X» (openspec/changes/fix (v2)/specs/a/spec.md:3) — y") is not None


def test_a_generator_batch_reads_was_and_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.step_size import CHANGE_DIFF_VIEW, _change_diff

    project = _project(tmp_path)
    _write(project, "openspec/changes/add-search/specs/owners/spec.md",
           "## MODIFIED Requirements\n### Requirement: Create\nCreate an owner.\n#### Scenario: Ok\n- WHEN saved\n- THEN listed\n")
    root = Path(_scan(project, monkeypatch, CHANGE)["run_root"])
    state = pilot_state.derive_state(root)
    row = _change_diff(project, root, state, state["attempts"][-1]["attempt_id"], "sha256:" + "0" * 64, True)
    assert row is not None and row["basis"] == CHANGE_DIFF_VIEW


def test_an_added_beside_a_modified_not_applied_is_asked() -> None:
    from tools.run_report import analyst_questions

    said = lines("c", ["owners"], [("ADDED", "owners", "Search", "Find an owner by «телефон»")],
                 [("MODIFIED", "owners", "Lookup", "Look an owner up by «телефон»", DELTA, 9, "x")], [], "")
    assert "- ADDED «Search» связан с неприменённым MODIFIED «Lookup»: его кейсы написаны по нынешнему тексту базы. Верно ли это?" in analyst_questions(said)


PARTIAL = "Объём неполный: применено 1 из 2 требований изменения add-search; не взято 1 — причины и вопросы в questions-for-analyst.md"


def test_the_report_shows_the_scope() -> None:
    from tools.run_report import _scope_lines

    assert _scope_lines({"scope": None, "openspec_scope": [SCOPE, PARTIAL]}) == ["## Охват после разметки", "", f"- {SCOPE}", f"- {PARTIAL}", ""]


def test_the_summary_says_the_scope_is_not_whole() -> None:
    from tools.run_report import _acceptance_lines

    acceptance = {"outcome": "ACCEPTED_WHOLE", "generated": 2, "accepted": 2, "removed": 0}
    assert PARTIAL in _acceptance_lines({"acceptance": acceptance, "openspec_scope": [SCOPE, PARTIAL]})


def test_the_report_takes_the_lines_in_their_order() -> None:
    from tools.run_report import openspec_scope_lines

    assert openspec_scope_lines([PARTIAL, "x — y", SCOPE]) == [SCOPE, PARTIAL]


def test_the_tail_of_the_summary() -> None:
    assert summary_tail("Кейсы приняты (2 из 2).", ["Не применено: MODIFIED «X» (a:1) — y"]) == "Кейсы приняты (2 из 2); объём неполный: 1 требований изменения не взято."


def test_another_run_has_no_marks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.model_records import openspec_snapshots

    scan = _scan(_project(tmp_path), monkeypatch, [BASE, *CHANGE])  # the spec.md named: the full regression of the area, as before
    root = Path(scan["run_root"])
    state = pilot_state.derive_state(root)
    rows = openspec_snapshots(root.parents[1], root, state, state["attempts"][-1])
    assert rows and not [row for row in rows if "base" in row or "context" in row]


def test_the_context_gives_no_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    _write(project, "openspec/specs/visits/spec.md", "## Requirements\n### Requirement: Visit\nBook a visit.\n#### Scenario: Ok\n- WHEN a\n- THEN b\n")
    scan = _scan(project, monkeypatch, CHANGE)
    _prepare(Path(scan["run_root"]), "context-marker:requirements", capsys)
    rows = _answer_of(Path(scan["run_root"]), scan["attempt_id"])["artifacts"]["analytics_documentation"]["requirements"]
    assert not [mark for row in rows for mark in row["provenance"] if mark.startswith("openspec/specs/") or "proposal" in mark]


def test_the_context_is_no_size_of_the_documents() -> None:
    huge = _entry("openspec/specs/visits/spec.md", "## Requirements\n" + "x" * (1024 * 1024 + 1), context=True)
    build_context(Path("."), docs_snapshot=[_entry(BASE, SPEC, base=True), _entry(DELTA, "## ADDED Requirements\n" + SEARCH), huge])
