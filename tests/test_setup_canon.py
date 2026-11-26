"""The setup of a case (canonical 1.2.0): machine setup without checks, bound to a precondition line; old documents stay as they were."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh
from tests.test_param_groups import grouped
from tests.test_reviewer_protocol import _new_run
from tools.batch_assembly import assemble_candidate, fill_fragment_mechanics
from tools.canonical_document import document_sha256, validate_canonical_document
from tools.orchestrate_test_case_revision import _mode_b
from tools.publish_test_case_bundle import planned_receipt
from tools.review_view import render
from tools.test_case_projections import DEFAULT_PROFILE as PROFILE, render_html_preview_validated
from tools.test_case_projections import render_markdown_validated, render_zephyr_csv

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
EXAMPLE_SHA256 = "sha256:0887cd3ee170c98bc945fe378c08af1985dc3d2a24aa466a3188061fa1c6aed7"  # of 1f11de6a, before the setup


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def with_setup(version: str = "1.2.0") -> dict:
    """The example with a setup on case 1: the operation and outputs of its first step, before that step."""
    document = _example()
    document["schema_version"] = version
    case = document["test_cases"][1]
    step = case["steps"][0]
    case["setup"] = [{"setup_id": "SETUP-B1-002-01", "display_order": 1, "precondition_index": 0,
                      "operation": copy.deepcopy(step["operation"]), "inputs": [], "outputs": copy.deepcopy(step["outputs"])}]
    return document


def _codes(document: dict) -> list[str]:
    from tools.canonical_document import validate_canonical_document

    return [row["code"] for row in validate_canonical_document(document)]


def test_a_case_with_setup_is_valid_in_1_2_0() -> None:
    assert _codes(with_setup()) == []


def test_setup_needs_1_2_0() -> None:
    assert _codes(with_setup("1.1.0")) == ["SCHEMA_CONST"]


def test_a_document_without_setup_keeps_its_hash() -> None:
    assert document_sha256(_example()) == EXAMPLE_SHA256


def test_a_setup_of_an_unknown_precondition_is_refused() -> None:
    document = with_setup()
    document["test_cases"][1]["setup"][0]["precondition_index"] = 3
    assert [(row["code"], row["message"]) for row in validate_canonical_document(document)] == [
        ("SETUP_PRECONDITION_UNKNOWN", "Подготовка SETUP-B1-002-01 ссылается на предусловие 3, а у кейса их 1")]


def test_a_step_expects_the_output_of_its_own_setup_only() -> None:
    document = with_setup()
    assertion = document["test_cases"][1]["steps"][0]["expectations"][0]["assertions"][0]
    assertion["expected"] = {"kind": "setup_output", "setup_id": "SETUP-B1-002-01", "output_id": "count"}
    assert _codes(document) == []
    assertion["expected"]["setup_id"] = "SETUP-other"
    assert "SEMANTIC_UNKNOWN_SETUP_OUTPUT" in _codes(document)


def test_the_cases_of_a_group_share_one_setup() -> None:
    document = grouped(version="1.2.0")
    members = [case for case in document["test_cases"] if case.get("param_group")]
    for number, case in enumerate(members):
        case["setup"] = [{"setup_id": f"SETUP-g{number}", "display_order": 1, "precondition_index": 0,
                          "operation": copy.deepcopy(members[0]["steps"][0]["operation"]), "inputs": [], "outputs": []}]
    assert _codes(document) == []
    members[1]["setup"][0]["precondition_index"] = 1
    members[1]["preconditions"].append("Ещё одно")
    assert "SEMANTIC_PARAM_GROUP_MISMATCH" in _codes(document)


def test_the_zephyr_csv_is_the_same_with_and_without_setup() -> None:
    assert render_zephyr_csv(with_setup()).payload == render_zephyr_csv(_example()).payload


def test_the_precondition_an_autotest_prepares_has_the_tail_in_mode_b_only() -> None:
    document = with_setup()
    document["test_cases"][1]["preconditions"] = ["Владелец создан"]  # its own line, not one of the common block
    for render in (render_html_preview_validated, render_markdown_validated):
        assert "готовит автотест" in render(document, PROFILE, {"setup_by_autotest": True}).payload.decode("utf-8")
        assert "готовит автотест" not in render(document, PROFILE).payload.decode("utf-8")


def test_the_views_show_the_setup() -> None:
    document = with_setup()
    assert "подготовка [SETUP-B1-002-01], без проверок:" in render(document).decode("utf-8")
    assert "- Подготовка [SETUP-B1-002-01] к предусловию 0 (до первого шага, без проверок)" in render(document, "autotest").decode("utf-8")


def test_the_code_fills_the_ids_and_orders_of_a_setup() -> None:
    case = with_setup()["test_cases"][1]
    for key in ("setup_id", "display_order"):
        del case["setup"][0][key]
    filled, summary = fill_fragment_mechanics({"status": "COMPLETE", "test_cases": [case], "diagnostics": []})
    assert (filled["test_cases"][0]["setup"][0]["setup_id"], filled["test_cases"][0]["setup"][0]["display_order"]) == ("SETUP-B1-002-01", 1)
    assert "setup_id ×1" in summary


def test_the_assembled_document_is_1_2_0_only_with_setup() -> None:
    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    assert assemble_candidate(header, plan, fragments)[0]["schema_version"] == "1.0.0"
    case = fragments[0]["test_cases"][0]
    case["preconditions"] = ["Платёж создан"]
    case["setup"] = [{"setup_id": "SETUP-a", "display_order": 1, "precondition_index": 0,
                      "operation": {"kind": "project_action", "capability_id": "CAP-shared"}, "inputs": [], "outputs": []}]
    _refresh(fragments[0])
    document = assemble_candidate(header, plan, fragments)[0]
    assert document["schema_version"] == "1.2.0" and validate_canonical_document(document) == []


def test_the_tail_comes_from_the_profile_of_the_attempt_not_from_setup(tmp_path: Path) -> None:
    document = with_setup()
    document["test_cases"][1]["preconditions"] = ["Владелец создан"]  # its own line: the tail would show
    cases_only = _mode_b(*_new_run(tmp_path / "a"))
    plain, tailed = (planned_receipt(document, tmp_path, mode_b=mode).preview_sha256 for mode in (False, True))
    assert (cases_only, _mode_b(*_new_run(tmp_path / "b", "local-pilot-v1"))) == (False, True)
    assert planned_receipt(document, tmp_path, mode_b=cases_only).preview_sha256 == plain != tailed
