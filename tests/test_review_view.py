"""FaT-CV: the reviewer reads a compact view of the cases; AUTO_FIX is a list of exact edits that code applies."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
NIGHT = Path(r"D:\AI-Projects\live\petclinic-fat-night-20261027\.pilot-runs\7a71393605b2482b989c9a824cf30188\cases\TCDOC-owner-lifecycle-night-7a713936.r1.json")
TARGET_BYTES = 320_000  # FaT-CV2: машинная часть шага для ревьюера, +61 КБ на ночном каноне; ради размера её не режем
TARGET_CASES = 52


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _role_bytes() -> int:
    from tools.step_size import role_files

    return sum(path.stat().st_size for path in role_files("tc-reviewer"))


# --- the view -----------------------------------------------------------------------------------

def test_the_view_of_the_example_fits_the_target_per_case_with_the_role() -> None:
    from tools.review_view import render

    document = _example()
    view = render(document)
    assert len(view) + _role_bytes() <= TARGET_BYTES * len(document["test_cases"]) / TARGET_CASES
    assert len(view) * 2 < len(EXAMPLE.read_bytes())


@pytest.mark.skipif(not NIGHT.is_file(), reason="the night canonical of 27.10 is on the work machine only")
def test_the_night_canonical_of_52_cases_is_at_most_320_kb_for_the_reviewer() -> None:
    from tools.review_view import render

    document = json.loads(NIGHT.read_text(encoding="utf-8"))
    assert len(document["test_cases"]) == TARGET_CASES
    assert len(render(document)) + _role_bytes() <= TARGET_BYTES


def test_the_view_is_the_same_bytes_for_the_same_candidate() -> None:
    from tools.review_view import render

    document = _example()
    shuffled = json.loads(json.dumps(document, sort_keys=True))
    assert render(document) == render(copy.deepcopy(document)) == render(shuffled)


def test_the_view_keeps_the_human_part_and_one_line_of_checks_and_no_raw_machine_keys() -> None:
    from tools.canonical_document import document_sha256
    from tools.review_view import render

    document = _example()
    view = render(document).decode("utf-8")
    case = document["test_cases"][0]
    step = next(step for step in case["steps"] if step["expectations"][0]["assertions"])
    for text in (case["case_id"], case["title"], case["objective"], *case["requirement_ids"], step["action"], step["test_data"],
                 step["expectations"][0]["text"], document["requirements"][0]["text"], document["source_requirements"][0]["text"],
                 document_sha256(document)):
        assert text in view
    assert "   Проверки: " in view
    for machine in ("input_id", "INPUT-", "capability_id", "semantic_type", "type_provenance", "ASSERT-", '"kind"'):
        assert machine not in view
    reviewed = view.split("reviewed_case_ids по порядку: ", 1)[1].split("\n", 1)[0].split(", ")
    assert reviewed == [item["case_id"] for item in document["test_cases"]]


def test_an_automation_blocker_is_shown_under_its_step_and_the_view_stays_deterministic() -> None:
    from tests.test_finish import _blocked_document
    from tools.review_view import render

    document = _blocked_document()
    view = render(document)
    step = document["test_cases"][0]["steps"][0]
    line = "   Блокер автоматизации [UNRESOLVED_OPERATION] /operation: Адрес сервиса не подтверждён."
    lines = view.decode("utf-8").splitlines()
    assert line in lines and lines.index(line) > next(i for i, text in enumerate(lines) if f"[{step['step_id']}]" in text)
    assert "BLOCK-products" not in view.decode("utf-8") and render(document) == view == render(copy.deepcopy(document))


def test_a_literal_input_missing_from_the_step_data_is_a_hint_not_a_stop() -> None:
    from tools.review_view import DATA_MISMATCH, data_mismatches, render

    document = _example()
    # FaT-CV2 (2.10): the only number of the example not named as a token is «3» of «через три дня» (written as a word)
    assert {value for hint in data_mismatches(document) for value in hint["values"]} == {"3"}
    for hint_case in document["test_cases"]:
        for hint_step in hint_case["steps"]:
            hint_step["test_data"] = (hint_step.get("test_data") or "") + " (3)"
    assert data_mismatches(document) == []
    case = document["test_cases"][0]
    step = next(step for step in case["steps"] if any(item["source"]["kind"] == "literal" for item in step["inputs"]))
    item = next(item for item in step["inputs"] if item["source"]["kind"] == "literal")
    item["source"]["value"] = "Zebra-Street-77"
    hints = data_mismatches(document)
    assert hints == [{"code": DATA_MISMATCH, "case_id": case["case_id"], "step_id": step["step_id"], "count": 1, "values": ["Zebra-Street-77"]}]
    assert f"{DATA_MISMATCH} {case['case_id']} {step['step_id']}" in render(document).decode("utf-8")
    item["source"]["value"] = "ab"  # shorter than MIN_LITERAL: no noise on «1» and «a»
    assert data_mismatches(document) == []
    item["source"]["value"] = "Zebra-Street-77"
    item["target"]["sensitive"] = True  # a sensitive value is never named
    assert data_mismatches(document) == []


def test_the_view_command_writes_the_view_of_the_assembled_candidate_only(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.canonical_document import document_sha256
    from tools.review_view import render

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    candidate = tmp_path / "r1.json"
    other = copy.deepcopy(document)
    other["test_cases"][0]["title"] = "Другой кандидат"
    candidate.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    command = [sys.executable, "-m", "tools.review_view", "--project", str(tmp_path), "--run", run_root.name, "--candidate", str(candidate)]
    _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=run_root / "bundle")  # publishes the assembled r1
    refused = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert refused.returncode == 2 and "not the assembled candidate" in json.loads(refused.stdout)["message"]
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    out = json.loads(done.stdout)
    assert done.returncode == 0 and out["candidate_sha256"] == document_sha256(document) and out["data_mismatch"] == 0
    assert Path(out["path"]).read_bytes() == render(document) and out["bytes"] == len(render(document))
    assert Path(out["path"]).parent == run_root / "review-views"


# --- the edits ----------------------------------------------------------------------------------

def _edit(document: dict, **overrides) -> dict:
    case = document["test_cases"][0]
    edit = {"correction_id": "FIX-cv", "case_id": case["case_id"], "field": "title", "old": case["title"], "new": "Уточнённый заголовок"}
    edit.update(overrides)
    return edit


def test_exact_edits_make_the_successor_and_pass_the_usual_checks() -> None:
    from tests.test_requirement_traceability import canonical_fixture as automated_document  # a plain step: free human text
    from tools.canonical_document import document_sha256, require_valid_canonical_document
    from tools.review_edits import apply_edits
    from tools.revision_selection import validate_successor

    document = automated_document()
    before = copy.deepcopy(document)
    case = document["test_cases"][0]
    step = case["steps"][0]
    expectation = step["expectations"][0]
    edits = [_edit(document),
             _edit(document, field="objective", old=case["objective"], new="Уточнённая цель."),
             _edit(document, field="precondition", index=0, old=case["preconditions"][0], new="Уточнённое предусловие."),
             _edit(document, field="action", step_id=step["step_id"], old=step["action"], new="Уточнённое действие."),
             _edit(document, field="test_data", step_id=step["step_id"], old=step["test_data"], new="Уточнённые данные."),
             _edit(document, field="expectation", step_id=step["step_id"], expectation_id=expectation["expectation_id"],
                   old=expectation["text"], new="Уточнённый результат.")]
    successor, rows = apply_edits(document, edits)
    assert rows == [] and document == before
    assert successor["revision"] == 2 and successor["parent_sha256"] == document_sha256(document)
    changed = successor["test_cases"][0]
    assert (changed["title"], changed["objective"], changed["preconditions"][0], changed["steps"][0]["action"],
            changed["steps"][0]["test_data"], changed["steps"][0]["expectations"][0]["text"]) == tuple(edit["new"] for edit in edits)
    require_valid_canonical_document(successor)
    assert validate_successor(document, successor) == []


@pytest.mark.parametrize(("overrides", "code"), [
    ({"old": "не тот текст"}, "EDIT_OLD_MISMATCH"),
    ({"field": "operation"}, "EDIT_FIELD_FORBIDDEN"),
    ({"field": "inputs"}, "EDIT_FIELD_FORBIDDEN"),
    ({"case_id": "TC-unknown"}, "EDIT_TARGET_UNKNOWN"),
    ({"field": "action", "step_id": "STEP-unknown"}, "EDIT_TARGET_UNKNOWN"),
    ({"field": "precondition", "index": 9}, "EDIT_TARGET_UNKNOWN"),
    ({"new": " "}, "EDIT_TEXT"),
    ({"correction_id": "FIX-other"}, "EDIT_CORRECTION_UNKNOWN"),
])
def test_an_edit_that_does_not_match_exactly_is_refused(overrides: dict, code: str) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.review_edits import apply_edits

    document = automated_document()
    successor, rows = apply_edits(document, [_edit(document, **overrides)], {"FIX-cv"})
    assert successor is None and [row["code"] for row in rows] == [code]


def test_one_text_is_edited_once_and_only_an_assembled_revision_1_is_edited() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.review_edits import apply_edits

    document = automated_document()
    twice = [_edit(document), _edit(document, new="Другой заголовок")]
    assert [row["code"] for row in apply_edits(document, twice)[1]] == ["EDIT_DUPLICATE"]
    assert apply_edits(document, [])[1][0]["code"] == "EDIT_LIST"
    parented = copy.deepcopy(document)
    parented["parent_sha256"] = "sha256:" + "1" * 64
    assert apply_edits(parented, [_edit(parented)])[1][0]["code"] == "EDIT_CANDIDATE"


def test_the_reviewer_output_has_exactly_one_successor_form() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import _review_rows
    from tools.review_edits import apply_edits

    document = automated_document()
    edits = [_edit(document)]
    successor, _rows = apply_edits(document, edits)
    reference = lambda value: {"document_id": value["document_id"], "revision": value["revision"], "document_sha256": document_sha256(value)}
    report = {"verdict": "AUTO_FIX_APPLIED", "candidate": reference(document), "effective": reference(successor),
              "reviewed_case_ids": [case["case_id"] for case in document["test_cases"]], "findings": [],
              "corrections": [{"id": "FIX-cv", "correction_kind": "MECHANICAL", "related_ids": [edits[0]["case_id"]],
                               "description": "Уточнить заголовок.", "evidence": ["CREQ"]}]}
    review = {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report, "successor_edits": edits}, "warnings": []}
    assert _review_rows(review) == []
    both = copy.deepcopy(review)
    both["artifacts"]["successor_document"] = successor
    assert _review_rows(both)
    accepted = copy.deepcopy(review)
    accepted["artifacts"]["validation_report"].update(verdict="ПРИНЯТО", effective=reference(document), corrections=[])
    assert _review_rows(accepted)
    machine = copy.deepcopy(review)
    machine["artifacts"]["successor_edits"][0]["field"] = "inputs"
    assert _review_rows(machine)


def test_the_edits_command_prints_the_effective_reference_for_the_report(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.review_edits import apply_edits

    document = automated_document()
    candidate, edits_path = tmp_path / "r1.json", tmp_path / "edits.json"
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    edits = [_edit(document)]
    edits_path.write_text(json.dumps(edits, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "tools.review_edits", "--candidate", str(candidate), "--edits", str(edits_path)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    successor, _rows = apply_edits(document, edits)
    assert done.returncode == 0 and json.loads(done.stdout)["effective"] == {
        "document_id": document["document_id"], "revision": 2, "document_sha256": document_sha256(successor)}
    edits_path.write_text(json.dumps([_edit(document, old="не тот")], ensure_ascii=False), encoding="utf-8")
    bad = subprocess.run([sys.executable, "-m", "tools.review_edits", "--candidate", str(candidate), "--edits", str(edits_path)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert bad.returncode == 1 and json.loads(bad.stdout)["diagnostics"][0]["code"] == "EDIT_OLD_MISMATCH"


# --- the path through the selection and the closure check ---------------------------------------

def _auto_fixed(tmp_path: Path, edits_for):
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    bundle_root = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=bundle_root, edits=edits_for(document))
    return run_root, attempt_id, document, session, package, review, receipt, bundle_root


def test_auto_fix_by_edits_selects_the_successor_and_binds_the_effective_canonical(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import _plain, orchestrate_revision
    from tools.pilot_state import read_effective_canonical
    from tools.review_edits import apply_edits

    run_root, attempt_id, document, session, package, review, receipt, bundle_root = _auto_fixed(tmp_path, lambda doc: [_edit(doc)])
    result = orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    successor, _rows = apply_edits(document, review["artifacts"]["successor_edits"])
    assert result.status == "EFFECTIVE_SELECTED" and _plain(result.effective_document) == successor
    assert _plain(read_effective_canonical(run_root, attempt_id)["document"]) == successor


def test_an_edit_that_does_not_apply_stops_the_selection_with_its_code(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

    run_root, attempt_id, document, session, package, review, receipt, bundle_root = _auto_fixed(tmp_path, lambda doc: [_edit(doc)])
    review = copy.deepcopy(review)
    review["artifacts"]["successor_edits"][0]["old"] = "не тот текст"
    with pytest.raises(OrchestrationError) as error:
        orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    assert "EDIT_OLD_MISMATCH" in json.dumps([dict(row) for row in error.value.diagnostics], ensure_ascii=False)


def test_the_closure_check_rebuilds_the_candidate_from_the_effective_document() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.review_edits import apply_edits, edits_rebuild

    document = automated_document()
    edits = [_edit(document)]
    successor, _rows = apply_edits(document, edits)
    assert edits_rebuild(successor, edits, document_sha256(document))
    forged = copy.deepcopy(successor)
    forged["test_cases"][0]["objective"] = "Подменённая цель."
    assert not edits_rebuild(forged, edits, document_sha256(document))
    assert not edits_rebuild(successor, edits, "sha256:" + "0" * 64)


# --- FaT-CV2: the machine part of each step, for the reviewer to judge its meaning -----------------

def _mvc_step(document: dict) -> tuple[dict, dict]:
    for case in document["test_cases"]:
        for step in case["steps"]:
            if any(item["target"]["name"] == "path" and item["source"]["kind"] == "literal" for item in step["inputs"]):
                return case, step
    raise AssertionError("the example has an MVC step")


def _step_lines(view: str, step_id: str) -> list[str]:
    lines = view.splitlines()
    start = next(index for index, text in enumerate(lines) if f"[{step_id}]" in text)
    end = next((index for index in range(start + 1, len(lines)) if not lines[index].startswith("   ")), len(lines))
    return lines[start:end]


def _operation_line(view: str, step_id: str) -> str:
    return next(text for text in _step_lines(view, step_id) if text.startswith("   Операция: "))


def test_each_step_shows_its_operation_inputs_and_outputs() -> None:
    from tools.review_view import render

    document = _example()
    _case, step = _mvc_step(document)
    capabilities = {item["capability_id"]: item for item in document["operation_capabilities"]}
    capability = capabilities[step["operation"]["capability_id"]]
    line = _operation_line(render(document).decode("utf-8"), step["step_id"])
    assert line.startswith(f"   Операция: {capability['capability_id']} ({capability['action']}) | входы: ")
    for item in sorted(step["inputs"], key=lambda row: row["display_order"]):
        assert f"{item['target']['name']}={json.dumps(item['source']['value'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}" in line
    assert line.endswith(" | выходы: " + (", ".join(row["output_id"] for row in step["outputs"]) or "—"))


def test_a_changed_capability_or_path_is_visible_to_the_reviewer() -> None:
    from tools.review_view import render

    document = _example()
    _case, step = _mvc_step(document)
    before = _operation_line(render(document).decode("utf-8"), step["step_id"])
    path = next(item for item in step["inputs"] if item["target"]["name"] == "path")
    path["source"]["value"] = "/vets"
    after = _operation_line(render(document).decode("utf-8"), step["step_id"])
    assert after != before and 'path="/vets"' in after


def test_a_sensitive_input_is_masked_and_a_long_literal_is_cut_with_a_mark_found_in_the_full_json() -> None:
    from tools.review_view import LITERAL_LIMIT, render

    document = _example()
    _case, step = _mvc_step(document)
    path = next(item for item in step["inputs"] if item["target"]["name"] == "path")
    path["source"]["value"] = "/owners/" + "x" * 400
    line = _operation_line(render(document).decode("utf-8"), step["step_id"])
    shown = json.dumps(path["source"]["value"], ensure_ascii=False)
    mark = f"…(+{len(shown) - LITERAL_LIMIT} симв.)"
    assert f"path={shown[:LITERAL_LIMIT]}{mark}" in line
    # the mark leads back to the same step of the full JSON: its step ID heads the block, its value starts with the shown part
    step_id = _step_lines(render(document).decode("utf-8"), step["step_id"])[0].split("[", 1)[1].split("]", 1)[0]
    found = next(row for case in document["test_cases"] for row in case["steps"] if row["step_id"] == step_id)
    assert json.dumps(next(item for item in found["inputs"] if item["target"]["name"] == "path")["source"]["value"],
                      ensure_ascii=False).startswith(shown[:LITERAL_LIMIT])
    # a sensitive input is masked in the view (the canonical schema rejects one today, the view never names it anyway)
    from tools.review_view import _input

    masked = copy.deepcopy(path)
    masked["target"]["sensitive"] = True
    assert _input(masked) == "path=***"


def test_the_view_lists_every_capability_with_its_action_and_test_boundary() -> None:
    from tools.review_view import LITERAL_LIMIT, render

    document = _example()
    view = render(document).decode("utf-8")
    section = view.split("## Операции (capabilities)\n", 1)[1].split("\n## ", 1)[0]
    for capability in document["operation_capabilities"]:
        line = next(text for text in section.splitlines() if text.startswith(f"- {capability['capability_id']}: "))
        assert line.startswith(f"- {capability['capability_id']}: {capability['action']}, adapter {capability['adapter']}")
        for argument in capability["arguments"]:
            assert f"{argument['name']}:{argument['semantic_type']['type']}{'*' if argument.get('required') else ''}" in line
        if capability.get("provenance"):
            first = str(capability["provenance"][0]).splitlines()[0]
            assert f"граница: {first[:LITERAL_LIMIT]}" in line
            assert len(first) <= LITERAL_LIMIT or f"…(+{len(first) - LITERAL_LIMIT} симв.)" in line


def test_the_header_says_code_checked_structure_not_meaning() -> None:
    from tools.review_view import VIEW_VERSION, render

    view = render(_example()).decode("utf-8")
    assert VIEW_VERSION == "review-view-v2"
    assert "Машинную часть" not in view
    assert "Код проверил структуру и ссылки" in view and "не смысл" in view and "проверяешь ты" in view


@pytest.mark.parametrize(("text", "value", "hinted"), [
    ("Ввести возраст 18", 17, True), ("Ввести возраст 18", 18, False), ("Ввести возраст 18", "18", False),
    ("Ввести возраст 170", 17, True), ("Ввести сумму 1.17", 17, True), ("Ввести 0", 0, False), ("Ввести 10", 0, True),
])
def test_a_number_of_any_length_is_compared_as_a_whole_token(text: str, value, hinted: bool) -> None:
    from tools.review_view import data_mismatches

    document = _example()
    _case, step = _mvc_step(document)
    item = copy.deepcopy(step["inputs"][0])
    item["source"]["value"] = value
    item["target"]["sensitive"] = False
    step.update(action=text, test_data="—", inputs=[item])
    assert bool([hint for hint in data_mismatches(document) if hint["step_id"] == step["step_id"]]) is hinted


# --- FaT-AV: the view for the automation generator ------------------------------------------------

_PROSE = {"test_data", "action", "text", "manual_reason", "provenance", "type_provenance"}


def _machine_leaves(value, skip=_PROSE):
    """Every scalar of the machine part a test needs: IDs, targets, types, literals, operators, sources."""
    if isinstance(value, dict):
        for key, item in value.items():
            # a literal is shown as its exact JSON, so its kind is the form of the value, not a word of the view
            if key not in skip and key != "display_order" and (key, item) != ("kind", "literal"):
                yield from _machine_leaves(item, skip)
    elif isinstance(value, list):
        for item in value:
            yield from _machine_leaves(item, skip)
    elif value is not None and not isinstance(value, bool):
        yield value


def _case_block(view: str, case_id: str) -> str:
    return view.split(f"\n### {case_id} ", 1)[1].split("\n### ", 1)[0]


def test_the_autotest_view_keeps_every_machine_value_whole_and_drops_what_the_generator_does_not_need() -> None:
    from tools.canonical_document import document_sha256
    from tools.review_view import LITERAL_LIMIT, render

    document = _example()
    _case, mvc = _mvc_step(document)
    long_path = "/owners/" + "x" * (LITERAL_LIMIT * 2)
    next(item for item in mvc["inputs"] if item["target"]["name"] == "path")["source"]["value"] = long_path
    view = render(document, profile="autotest").decode("utf-8")
    assert document_sha256(document) in view and json.dumps(long_path) in view and "симв.)" not in view
    for case in document["test_cases"]:
        block = _case_block(view, case["case_id"])
        assert case["objective"] in block and all(text in block for text in case["preconditions"])
        for step in case["steps"]:
            assert step["action"] in block and all(row["text"] in block for row in step["expectations"])
            for leaf in _machine_leaves(step):
                shown = json.dumps(leaf, ensure_ascii=False)
                assert str(leaf) in block or shown in block, (step["step_id"], leaf)
    for capability in document["operation_capabilities"]:
        assert all(str(line) in view for line in capability.get("provenance") or [])  # the test boundary, whole
        assert all(str(leaf) in view for leaf in _machine_leaves(capability))
    kept = "\n".join([row["text"] for row in document["requirements"]]
                     + [str(line) for row in document["operation_capabilities"] for line in row["provenance"]]
                     + [text for case in document["test_cases"] for text in (case["objective"], *case["preconditions"])]
                     + [text for case in document["test_cases"] for step in case["steps"]
                        for text in (step["action"], *(row["text"] for row in step["expectations"]))])
    gone = [step["test_data"] for case in document["test_cases"] for step in case["steps"] if step.get("test_data")]
    gone += [row["text"] for row in document["source_requirements"]]
    gone = [text for text in gone if text not in kept]  # a test data equal to a kept text is that text, not the field
    assert gone and not [text for text in gone if text in view] and "Данные:" not in view
    # campaign 28.10: 42–50 % of the canonical JSON on the 6 projects, the example 53 %
    assert len(view.encode("utf-8")) < 0.6 * len(EXAMPLE.read_bytes())


def test_the_autotest_view_is_deterministic_and_is_not_the_review_view() -> None:
    from tools.review_view import render

    document = _example()
    shuffled = json.loads(json.dumps(document, sort_keys=True))
    view = render(document, profile="autotest")
    assert view == render(copy.deepcopy(document), profile="autotest") == render(shuffled, profile="autotest")
    assert view != render(document) and render(document) == render(document, profile="review")
    with pytest.raises(ValueError, match="profile"):
        render(document, profile="other")


def test_the_view_command_writes_the_autotest_view_of_the_effective_document_only(tmp_path: Path) -> None:
    from tests.test_finish import _project
    from tools.pilot_state import read_effective_canonical
    from tools.review_view import render

    project, run_root, attempt_id, _values = _project(tmp_path)
    effective = read_effective_canonical(run_root, attempt_id)
    command = [sys.executable, "-m", "tools.review_view", "--project", str(project), "--run", run_root.name]
    both = subprocess.run([*command, "--effective", "--candidate", str(tmp_path / "x.json")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert both.returncode != 0
    done = subprocess.run([*command, "--effective"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    out = json.loads(done.stdout)
    data = render(effective["document"], profile="autotest")
    assert done.returncode == 0 and out["candidate_sha256"] == effective["document_digest"] and out["profile"] == "autotest", out
    assert Path(out["path"]) == run_root / "review-views" / (effective["document_digest"].removeprefix("sha256:") + ".autotest.md")
    assert Path(out["path"]).read_bytes() == data and out["bytes"] == len(data)


def _variant_step() -> dict:
    """FaT-AV: a step with every target, value source, output source, assertion actual and expected of the schema."""
    typed = {"semantic_type": {"kind": "json", "type": "string", "format": "uuid"}, "type_provenance": ["тип из OpenAPI owners.yaml"]}
    inputs = [
        {"input_id": "INPUT-V-1", "display_order": 1, "target": {"location": "path", "name": "ownerId", "sensitive": False},
         "source": {"kind": "step_output", "step_id": "STEP-V-0", "output_id": "owner_id"}, "semantic_type": {"kind": "json", "type": "integer"}},
        {"input_id": "INPUT-V-2", "display_order": 2, "target": {"location": "query", "name": "lastName", "sensitive": False},
         "source": {"kind": "literal", "value": "Фамилия «с кавычками» \"x\""}, "semantic_type": {"kind": "json", "type": "string"}},
        {"input_id": "INPUT-V-3", "display_order": 3, "target": {"location": "header", "name": "X-Tenant", "sensitive": False},
         "source": {"kind": "environment", "name": "TENANT_ID"}, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["env"]},
        {"input_id": "INPUT-V-4", "display_order": 4, "target": {"location": "body", "pointer": "/owner/address/city", "sensitive": False},
         "source": {"kind": "fixture", "name": "city_fixture"}, **typed},
        {"input_id": "INPUT-V-5", "display_order": 5, "target": {"location": "header", "name": "Authorization", "sensitive": True},
         "source": {"kind": "secret_handle", "handle": "secret://petclinic/api-token", "safe_label": "API token"},
         "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["auth"]},
        {"input_id": "INPUT-V-6", "display_order": 6, "target": {"location": "arg", "name": "form_fields", "sensitive": False},
         "source": {"kind": "literal", "value": {"a": [1, 2.5, None, True]}}, "semantic_type": {"kind": "json", "type": "object"}},
    ]
    outputs = [
        {"output_id": "status", "display_order": 1, "source": {"kind": "http_status"}, "semantic_type": {"kind": "json", "type": "integer"}},
        {"output_id": "location", "display_order": 2, "source": {"kind": "http_header", "name": "Location"}, "semantic_type": {"kind": "json", "type": "string"}},
        {"output_id": "count", "display_order": 3, "source": {"kind": "project_result", "name": "owner_count"}, "semantic_type": {"kind": "json", "type": "integer"}},
        {"output_id": "body_id", "display_order": 4, "source": {"kind": "http_body", "pointer": "/id"}, **typed},
    ]
    actuals = [{"kind": "http_status"}, {"kind": "http_header", "name": "Content-Type"}, {"kind": "project_result", "name": "view_name"},
               {"kind": "http_body", "pointer": "/errors/0/field", **typed}, {"kind": "step_output", "step_id": "STEP-V-0", "output_id": "owner_id"}]
    expecteds = [{"kind": "literal", "value": 201}, {"kind": "step_output", "step_id": "STEP-V-0", "output_id": "owner_id"},
                 {"kind": "fixture", "name": "expected_view", **typed}, {"kind": "environment", "name": "EXPECTED_CT", **typed},
                 {"kind": "secret_handle", "handle": "secret://h2", "safe_label": "пароль", **typed},
                 {"kind": "regex", "dialect": "portable-regex-v1", "pattern": r"^owners/\d+$"},
                 {"kind": "schema_ref", "uri": "schemas/owner.json", "sha256": "sha256:" + "a" * 64, "draft": "2020-12", "provenance": ["api.yaml"]}]
    assertions = [{"assertion_id": f"ASSERT-V-{index}", "display_order": index, "actual": actuals[index % len(actuals)], "operator": "equals",
                   "expected": expected} for index, expected in enumerate(expecteds, start=1)]
    assertions.append({"assertion_id": "ASSERT-V-9", "display_order": 9, "actual": actuals[0], "operator": "exists"})
    return {"step_id": "STEP-V-1", "display_order": 1, "action": "Создать владельца", "test_data": "—", "manual_only": False, "manual_reason": None,
            "operation": {"kind": "http", "binding_profile": "http-binding-v1", "method": "POST", "path": "/owners/{ownerId}",
                          "base_url_source": {"kind": "environment", "name": "BASE_URL", "provenance": ["application.yml server.port"]}},
            "inputs": inputs, "outputs": outputs,
            "automation_blockers": [{"blocker_id": "BLOCK-V-1", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Адрес?",
                                     "provenance": ["нет в документации"]}],
            "expectations": [{"expectation_id": "EXP-V-1", "display_order": 1, "text": "Владелец создан", "assertions": assertions}]}


def test_the_autotest_step_loses_no_machine_value_of_any_target_source_or_assertion() -> None:
    """FaT-AV (старшая 3): every leaf of operation, inputs, outputs, assertions and blockers (without blocker provenance)
    is in the bytes of the view — a body target keeps its pointer, a secret input its handle and safe label."""
    from tools.review_view import _autotest_step

    step = _variant_step()
    block = "\n".join(_autotest_step(step))
    machine = {key: step[key] for key in ("operation", "inputs", "outputs", "expectations")}
    machine["automation_blockers"] = [{key: value for key, value in row.items() if key != "provenance"} for row in step["automation_blockers"]]
    for leaf in _machine_leaves(machine, skip={"text"}):
        shown = json.dumps(leaf, ensure_ascii=False)
        assert str(leaf) in block or shown in block, leaf
    assert "body:/owner/address/city" in block and "None" not in block
    secret = next(line for line in block.splitlines() if "INPUT-V-5" in line)
    assert "(секрет)" in secret and "secret://petclinic/api-token" in secret and "API token" in secret and "***" not in secret


def test_the_autotest_view_keeps_the_canonical_requirements_the_cases_link() -> None:
    """FaT-AV (старшая 3): the testing-work constraints live in the canonical requirements (automation contract)."""
    from tools.review_view import render

    document = _example()
    view = render(document, profile="autotest").decode("utf-8")
    section = view.split("\n## Требования\n", 1)[1].split("\n## Операции (capabilities)\n", 1)[0]  # a text may hold its own headers
    for requirement in document["requirements"]:
        assert f"- {requirement['requirement_id']}: {requirement['text']}" in section
    canonical = "\n".join(row["text"] for row in document["requirements"])
    unique = [row["text"] for row in document["source_requirements"] if row["text"] not in canonical]
    assert unique and not [text for text in unique if text in view]  # a source text kept as a canonical one stays
