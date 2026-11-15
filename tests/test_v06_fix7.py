"""fix7: the capabilities of a document lie in the canon order (by capability_id), however the batches declared them."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import prepare_stage, submit_stage
from tests.test_digest_traps import _publish_inputs
from tests.test_review_requested import _generated_run

PET_ADD = {"capability_id": "CAP-pet-add", "adapter": "native", "action": "add-pet", "arguments": [], "results": [], "provenance": ["source:cap"]}
OWNER_CREATE = dict(PET_ADD, capability_id="CAP-owner-create", action="create-owner")


def _with_capabilities(fragment: dict, capabilities: list[dict]) -> dict:
    fragment["operation_capabilities"] = capabilities
    fragment.pop("digest", None)
    return fragment


@pytest.mark.parametrize("copy_of_batch_one", [False, True], ids=["without_copy", "with_copy"])
def test_a_later_batch_with_a_smaller_new_capability_id_is_accepted_in_canon_order(tmp_path: Path, copy_of_batch_one: bool) -> None:
    run = _generated_run(tmp_path, generated=False)
    _publish_inputs(run)
    first, second = (json.loads(Path(path).read_bytes()) for path in run["fragments"])
    root = run["run_root"]
    stage_one = f"tc-generator:{first['batch_id']}"
    assert prepare_stage(root, stage_one)[0] == 0
    assert submit_stage(root, stage_one, _with_capabilities(first, [PET_ADD]))[0] == 0
    stage_two = f"tc-generator:{second['batch_id']}"
    assert prepare_stage(root, stage_two)[0] == 0
    code, out = submit_stage(root, stage_two, _with_capabilities(second, [OWNER_CREATE, PET_ADD] if copy_of_batch_one else [OWNER_CREATE]))
    assert code == 0, out
    from tools.batch_assembly import assemble_candidate, with_digest

    header = {key: value for key, value in json.loads(Path(run["header"]).read_bytes()).items() if key != "digest"}
    document, _receipt = assemble_candidate(header, json.loads(Path(run["plan"]).read_bytes()), [with_digest(first), with_digest(second)])
    assert [item["capability_id"] for item in document["operation_capabilities"]] == ["CAP-owner-create", "CAP-pet-add"]


def _operation_case_fragments(capability_of_batch_two: str) -> tuple[dict, dict, list[dict]]:
    """Two batches whose cases have one form; the step of each calls a capability of the document (batch two: *capability_of_batch_two*)."""
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    result = [{"name": "failed", "semantic_type": {"kind": "json", "type": "boolean"}}]
    capabilities = [dict(PET_ADD, results=result), dict(PET_ADD, capability_id="CAP-pet-del", action="del-pet", results=result)]
    for fragment, used in zip(fragments, ["CAP-pet-add", capability_of_batch_two]):
        fragment["operation_capabilities"] = capabilities
        step = fragment["test_cases"][0]["steps"][0]
        step.update(manual_only=False, manual_reason=None, operation={"kind": "project_action", "capability_id": used})
        step["expectations"][0]["assertions"] = [{"assertion_id": "ASSERT-" + step["step_id"].removeprefix("STEP-"), "display_order": 1,
                                                  "actual": {"kind": "project_result", "name": "failed"}, "operator": "equals",
                                                  "expected": {"kind": "literal", "value": False}}]
        _refresh(fragment)
    return header, plan, fragments


def _duplicates(header: dict, plan: dict, fragments: list[dict]) -> list[dict]:
    from tools.batch_assembly import assemble_candidate

    return [item for item in assemble_candidate(header, plan, fragments)[1]["warnings"] if item["code"] == "POSSIBLE_DUPLICATE"]


def test_two_cases_that_differ_only_in_the_operation_are_not_a_possible_duplicate() -> None:
    assert _duplicates(*_operation_case_fragments("CAP-pet-del")) == []


def test_two_cases_that_differ_only_in_their_own_ids_are_still_a_possible_duplicate() -> None:
    (warning,) = _duplicates(*_operation_case_fragments("CAP-pet-add"))
    assert len(warning["case_ids"]) == 2


def test_a_member_of_an_over_budget_group_that_is_over_the_budget_alone_has_its_own_note() -> None:
    from tests.test_batch_size_split import _evidence, _requirement
    from tools.batch_assembly import plan_batches

    requirements = [_requirement(number, f"FR-{number} " + ("б" * 5000 if number == 3 else "а" * 400)) for number in range(1, 6)]
    requirements.append(_requirement(6, "NFR-1 " + "в" * 400))
    plan = plan_batches(requirements, _evidence(), window_bytes=400_000, input_bytes=100_000)

    assert any(note.startswith("BATCH_GROUP_OVER_BUDGET: группа") and "разрезана" in note for note in plan.notes), plan.notes  # the group was cut
    assert any(note.startswith("BATCH_GROUP_OVER_BUDGET") and "требование SREQ-0003" in note and "не делится" in note for note in plan.notes), plan.notes


@pytest.mark.parametrize(("capability_of_batch_two", "digest"), [
    (None, "sha256:153e8d603a77084c5e2c660b58566d29ad22999f13bafe364587c66cc21b911c"),
    ("CAP-pet-del", "sha256:1f8ec35f67f63e2f8a05a9c52f08aa00e9b229ff3ffd748796f14daab404cb94"),
    ("CAP-pet-add", "sha256:d229a3d3070a8fff0cd3cc802c2db2401f64019c251962276b26a9c288b1e091"),
])
def test_the_candidate_of_an_already_valid_document_keeps_its_bytes(capability_of_batch_two: str | None, digest: str) -> None:
    """The digests were taken on the code before fix7 (ddb8074): a valid document lay in canon order already, the sort changes nothing."""
    from tools.batch_assembly import assemble_candidate

    if capability_of_batch_two is None:
        from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan

        header, context = _header(), _context_receipt()
        plan = _plan(header, context)
        arguments = (header, plan, [_fragment(plan, context, 0), _fragment(plan, context, 1)])
    else:
        arguments = _operation_case_fragments(capability_of_batch_two)
    assert assemble_candidate(*arguments)[1]["document_digest"] == digest


def test_two_cases_that_differ_only_in_the_ids_of_their_own_outputs_are_a_possible_duplicate() -> None:
    header, plan, fragments = _operation_case_fragments("CAP-pet-add")
    from tests.test_batch_assembly import _refresh

    for fragment, name in zip(fragments, ["id_one", "id_two"]):
        fragment["test_cases"][0]["steps"][0]["outputs"] = [{"output_id": name, "display_order": 1, "source": {"kind": "project_result", "name": "failed"},
                                                             "semantic_type": {"kind": "json", "type": "boolean"}}]
        _refresh(fragment)
    (warning,) = _duplicates(header, plan, fragments)
    assert len(warning["case_ids"]) == 2
