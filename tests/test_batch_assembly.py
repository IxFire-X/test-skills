import hashlib
import json
from pathlib import Path

import pytest

from tests.helpers import prepare_stage, submit_stage
from tools.batch_assembly import BatchAssemblyError, assemble_candidate, canonical_bytes, validate_fragment
from tools.batch_assembly import plan_batches, with_digest
from tools.canonical_document import validate_canonical_document
from tools.schema_validation import schema_diagnostics


def _digest(letter: str) -> str:
    return "sha256:" + letter * 64


def _header_digest(header: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _self_digest(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _context_receipt() -> dict:
    receipt = {
        "schema_version": "1.0.0", "inventory_digest": _digest("e"), "batch_index": 1,
        "files": [], "byte_count": 0, "byte_set_digest": _digest("f"),
    }
    receipt["digest"] = _self_digest(receipt)
    return receipt


def _header():
    return {
        "schema_version": "1.0.0",
        "document_id": "TCDOC-pilot",
        "revision": 1,
        "parent_sha256": None,
        "content_locale": "ru-RU",
        "metadata": {"subject": {"kind": "generic", "name": "Платежи"}, "documentation": ["source"], "project": "pilot", "author": "controller", "date": "2026-08-26"},
    }


def _requirements():
    return [
        {"source_requirement_id": "SREQ-payment", "display_order": 1, "text": "Оплата", "provenance": ["source:payment"], "digest": _digest("a")},
        {"source_requirement_id": "SREQ-refund", "display_order": 2, "text": "Возврат", "provenance": ["source:refund"], "digest": _digest("b")},
    ]


def _plan(header, context, requirements=None):
    from tools.batch_assembly import canonical_bytes, plan_batches

    requirements = _requirements() if requirements is None else requirements
    # FaT-BO: a window that leaves room for one requirement's expected answer splits the two into two batches
    input_bytes = int(10_000 * 0.85) - len(canonical_bytes(requirements)) - 600
    return plan_batches(requirements, {"header_digest": _header_digest(header), "context_receipt_digest": context["digest"]},
                        window_bytes=10_000, input_bytes=input_bytes)


def _fragment(plan, context, batch_index: int):
    batch = plan["batches"][batch_index]
    source_id = batch["owned_source_requirement_ids"][0]
    suffix = source_id.removeprefix("SREQ-")
    namespace = batch["namespace"]
    canonical_id = f"CREQ-{namespace}-{suffix}"
    fragment = {
        "schema_version": "1.0.0",
        "status": "COMPLETE",
        "batch_id": batch["batch_id"],
        "namespace": batch["namespace"],
        "plan_digest": plan["digest"],
        "header_digest": plan["header_digest"],
        "context_receipt_digest": context["digest"],
        "context_receipt": context,
        "owned_source_requirement_ids": [source_id],
        "requirements": [{"requirement_id": canonical_id, "display_order": 99, "text": "Требование " + suffix, "provenance": ["source:" + suffix]}],
        "source_to_canonical_mappings": [{"source_requirement_id": source_id, "canonical_requirement_ids": [canonical_id]}],
        "operation_capabilities": [{"capability_id": "CAP-shared", "adapter": "native", "action": "call", "arguments": [], "results": [], "provenance": ["source:cap"]}],
        "test_cases": [{"case_id": f"TC-{namespace}-{suffix}", "display_order": 99, "requirement_ids": [canonical_id], "title": "Проверка", "objective": "Проверить сценарий", "categories": ["functional"], "priority": "MEDIUM", "preconditions": [], "management": {"status": None, "folder": None, "components": [], "labels": [], "owner": None, "estimated_time": None, "external_keys": {}, "external_links": {"issues": [], "pages": []}, "custom_fields": {}}, "steps": [{"step_id": f"STEP-{namespace}-{suffix}", "display_order": 1, "action": "Выполнить действие", "test_data": "Данные", "manual_only": True, "manual_reason": "Ручная проверка", "automation_blockers": [], "operation": None, "inputs": [], "outputs": [], "expectations": [{"expectation_id": f"EXP-{namespace}-{suffix}", "display_order": 1, "text": "Результат", "assertions": []}]}]}],
        "diagnostics": [],
    }
    fragment["digest"] = _self_digest(fragment)
    return fragment


def _refresh(fragment: dict) -> None:
    fragment["digest"] = _self_digest(fragment)


def test_generator_envelope_validates_nested_context_receipt(pack_root: Path):
    context = _context_receipt()
    fragment = _fragment(_plan(_header(), context), context, 0)
    output = {"schema_version": "5.0.0", "stage": "tc-generator",
              "artifacts": {"candidate_fragment": fragment}, "warnings": []}
    schema = pack_root / "schemas/tc-generator-output.schema.json"
    assert schema_diagnostics(output, schema, pack_root) == []
    context["byte_count"] = "invalid"
    diagnostics = schema_diagnostics(output, schema, pack_root)
    assert any(row["path"] == "/artifacts/candidate_fragment/context_receipt/byte_count"
               and row["code"] == "SCHEMA_TYPE" for row in diagnostics)


def test_assemble_candidate_is_byte_identical_for_shuffled_fragments_and_keeps_duplicate_warning():
    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    for index, fragment in enumerate(fragments):
        validate_fragment(fragment, plan, header, context, fragments[:index])  # FaT-BO: batch 2 goes after batch 1

    first, first_receipt = assemble_candidate(header, plan, fragments)
    second, second_receipt = assemble_candidate(header, plan, list(reversed(fragments)))

    assert canonical_bytes(first) == canonical_bytes(second)
    assert canonical_bytes(first_receipt) == canonical_bytes(second_receipt)
    assert validate_canonical_document(first) == []
    assert [case["display_order"] for case in first["test_cases"]] == [1, 2]
    assert [item["code"] for item in first_receipt["warnings"]] == ["POSSIBLE_DUPLICATE"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda fragment: fragment.update({"batch_id": "BATCH-foreign"}), "BATCH_ID_CONFLICT"),
        (lambda fragment: fragment["test_cases"][0].update({"requirement_ids": ["CREQ-refund"]}), "BATCH_PARTITION_CONFLICT"),
    ],
)
def test_validate_fragment_rejects_identity_and_cross_batch_requirement_conflicts(mutation, code):
    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragment = _fragment(plan, context, 0)
    mutation(fragment)
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match=code):
        validate_fragment(fragment, plan, header, context)


def test_assemble_candidate_rejects_same_capability_id_with_different_semantics():
    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    fragments[1]["operation_capabilities"][0]["action"] = "different"
    _refresh(fragments[1])
    with pytest.raises(BatchAssemblyError, match="BATCH_CAPABILITY_CONFLICT"):  # FaT-BO: refused with the batch, before assembly
        assemble_candidate(header, plan, fragments)


def test_assemble_candidate_rejects_a_canonical_requirement_reused_across_batches():
    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    fragments[1]["requirements"][0]["requirement_id"] = "CREQ-B1-payment"
    fragments[1]["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B1-payment"]
    fragments[1]["test_cases"][0]["requirement_ids"] = ["CREQ-B1-payment"]
    _refresh(fragments[1])

    with pytest.raises(BatchAssemblyError, match="BATCH_ID_CONFLICT"):
        assemble_candidate(header, plan, fragments)


def test_validate_fragment_fails_closed_for_schema_drift_namespace_and_tampered_context_receipt():
    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragment = _fragment(plan, context, 0)
    fragment["requirements"][0]["unexpected"] = True
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(fragment, plan, header, context)

    fragment = _fragment(plan, context, 0)
    fragment["requirements"][0]["requirement_id"] = "CREQ-B2-payment"
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B2-payment"]
    fragment["test_cases"][0]["requirement_ids"] = ["CREQ-B2-payment"]
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match="BATCH_ID_CONFLICT"):
        validate_fragment(fragment, plan, header, context)

    fragment = _fragment(plan, context, 0)
    tampered = dict(context)
    tampered["byte_count"] = 1
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(fragment, plan, header, tampered)


def test_duplicate_mapping_is_partition_conflict(tmp_path: Path):
    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    duplicate = _fragment(plan, context, 0)
    duplicate["source_to_canonical_mappings"].append(dict(duplicate["source_to_canonical_mappings"][0]))
    _refresh(duplicate)
    with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
        validate_fragment(duplicate, plan, header, context)

    conflicting = _fragment(plan, context, 0)
    conflicting["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B1-unknown"]
    _refresh(conflicting)
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(conflicting, plan, header, context)

    validate_fragment(_fragment(plan, context, 1), plan, header, context, [_fragment(plan, context, 0)])


# test_v06_fix7.py until П4 — fix7: the capabilities of a document lie in the canon order (by capability_id), however
# the batches declared them.


PET_ADD = {"capability_id": "CAP-pet-add", "adapter": "native", "action": "add-pet", "arguments": [], "results": [], "provenance": ["source:cap"]}
OWNER_CREATE = dict(PET_ADD, capability_id="CAP-owner-create", action="create-owner")


def _with_capabilities(fragment: dict, capabilities: list[dict]) -> dict:
    fragment["operation_capabilities"] = capabilities
    fragment.pop("digest", None)
    return fragment


@pytest.mark.parametrize("copy_of_batch_one", [False, True], ids=["without_copy", "with_copy"])
def test_a_later_batch_with_a_smaller_new_capability_id_is_accepted_in_canon_order(tmp_path: Path, copy_of_batch_one: bool) -> None:
    from tests.test_digest_traps import _publish_inputs  # not at the top: both import this file
    from tests.test_review_requested import _generated_run

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

    header = {key: value for key, value in json.loads(Path(run["header"]).read_bytes()).items() if key != "digest"}
    document, _receipt = assemble_candidate(header, json.loads(Path(run["plan"]).read_bytes()), [with_digest(first), with_digest(second)])
    assert [item["capability_id"] for item in document["operation_capabilities"]] == ["CAP-owner-create", "CAP-pet-add"]


def _operation_case_fragments(capability_of_batch_two: str) -> tuple[dict, dict, list[dict]]:
    """Two batches whose cases have one form; the step of each calls a capability of the document (batch two: *capability_of_batch_two*)."""
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
    from tests.test_batch_size_split import _evidence, _requirement  # not at the top: test_batch_size_split imports this file

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
    if capability_of_batch_two is None:
        header, context = _header(), _context_receipt()
        plan = _plan(header, context)
        arguments = (header, plan, [_fragment(plan, context, 0), _fragment(plan, context, 1)])
    else:
        arguments = _operation_case_fragments(capability_of_batch_two)
    assert assemble_candidate(*arguments)[1]["document_digest"] == digest


def test_two_cases_that_differ_only_in_the_ids_of_their_own_outputs_are_a_possible_duplicate() -> None:
    header, plan, fragments = _operation_case_fragments("CAP-pet-add")

    for fragment, name in zip(fragments, ["id_one", "id_two"]):
        fragment["test_cases"][0]["steps"][0]["outputs"] = [{"output_id": name, "display_order": 1, "source": {"kind": "project_result", "name": "failed"},
                                                             "semantic_type": {"kind": "json", "type": "boolean"}}]
        _refresh(fragment)
    (warning,) = _duplicates(header, plan, fragments)
    assert len(warning["case_ids"]) == 2
