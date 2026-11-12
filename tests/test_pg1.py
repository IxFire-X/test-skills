"""PG1 (Petclinic v0.4, 06.11): the param_group rules are decided by one batch — the check of a fragment and model-response of a
generator batch refuse a group error before the answer is written, with the rows assemble's audit gives, and the stage keeps
its one retry; assemble checks as before.

A group lives in one batch: its cases share requirement_ids (the group shape keeps them), and a canonical requirement belongs
to the batch whose namespace it carries. So the rules on the cases of one batch say what the audit says of the whole
document, and a group name an earlier batch took is refused at the later batch, whose answer can still be replaced. Before:
three such errors passed the check of a fragment and model-response, assemble refused, the recorded answer could not be replaced
(MODEL_STAGE_ANSWERED) — the one way out was a new run.

The rules that need the whole document (Russian prose anywhere in it) run at the last batch of the plan, on the document
assemble builds from the recorded answers and this one: assemble refuses nothing the last model-response accepted."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_once
from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _requirements
from tests.test_batch_journal import _prior, _response, _second_request
from tests.test_digest_traps import _generator_request, _publish_inputs
from tests.test_review_requested import _api, _generated_run, _journal, _put

HEADER = dict(_header(), schema_version="1.1.0")  # param_group is of the 1.1.0 canon
EXPECTED = {
    "undeclared": "SEMANTIC_PARAM_GROUP_PARAMETER",  # Petclinic: «not declared in the design of its requirements»
    "none varies": "SEMANTIC_PARAM_GROUP_PARAMETER",  # Petclinic: «exactly one design parameter must vary, not none»
    "two vary": "SEMANTIC_PARAM_GROUP_PARAMETER",
    "single": "SEMANTIC_PARAM_GROUP_SINGLE",
    "mismatch": "SEMANTIC_PARAM_GROUP_MISMATCH",
}


def _break(edit: str | None, first: dict, second: dict) -> None:
    """Break one param_group rule on the two variants of a group (the same edit on a fragment and on a candidate)."""
    if edit == "undeclared":
        for case in (first, second):
            case["design_refs"] = [dict(ref, parameter="P-unknown") if ref["parameter"] == "P-amount" else ref for ref in case["design_refs"]]
    elif edit == "none varies":
        second["design_refs"] = copy.deepcopy(first["design_refs"])
    elif edit == "two vary":
        second["design_refs"][1]["class"] = "C-currency-2"
    elif edit == "single":
        del second["param_group"]
    elif edit == "mismatch":
        second["priority"] = "LOW"


def _grouped(fragment: dict, edit: str | None = None, group: str = "PG-amount") -> dict:
    """The batch *fragment* with its one case as a group of two variants of the design parameter P-amount."""
    from tools.batch_assembly import with_digest

    grouped = {key: copy.deepcopy(value) for key, value in fragment.items() if key != "digest"}
    grouped["requirements"][0]["design"] = {"parameters": [
        {"id": "P-amount", "name": "сумма", "valid": [{"id": "C-amount-1"}, {"id": "C-amount-2"}], "invalid": []},
        {"id": "P-currency", "name": "валюта", "valid": [{"id": "C-currency-1"}, {"id": "C-currency-2"}], "invalid": []},
    ]}
    first = grouped["test_cases"][0]
    second = copy.deepcopy(first)
    second["case_id"] += "-V2"
    second["display_order"] = first["display_order"] + 1
    second["title"] = first["title"] + " (вариант 2)"
    step = second["steps"][0]
    step["step_id"] += "-V2"
    step["expectations"][0]["expectation_id"] += "-V2"
    for index, case in enumerate((first, second), start=1):
        case["param_group"] = group
        case["technique"] = "EP"
        case["design_refs"] = [{"parameter": "P-amount", "class": f"C-amount-{index}"}, {"parameter": "P-currency", "class": "C-currency-1"}]
    _break(edit, first, second)
    grouped["test_cases"] = [first, second]
    return with_digest(grouped)


def _split() -> tuple[dict, dict]:
    context = _context_receipt()
    return _plan(HEADER, context), context


def _group_rows(rows: list[dict]) -> list[tuple[str, str, str]]:
    """(path, code, the audit's text) — the check of a fragment puts the text after «… (of this fragment) — »."""
    return [(row["path"], row["code"], row["message"].rsplit(" — ", 1)[-1]) for row in rows if row["code"].startswith("SEMANTIC_PARAM_GROUP_")]


@pytest.mark.parametrize("edit", sorted(EXPECTED))
def test_validate_fragment_refuses_a_group_error_of_the_batch_with_the_rows_of_the_document_audit(edit: str) -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.canonical_document import validate_canonical_document_all

    plan, context = _split()
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(_grouped(_fragment(plan, context, 0), edit), plan, HEADER, context)
    rows = refused.value.diagnostics
    assert refused.value.code == "BATCH_SEMANTIC_CONFLICT" and {row["code"] for row in rows} == {EXPECTED[edit]}, rows
    assert all(row["fix"] and row["path"] for row in rows), rows

    # the same rule on the whole document: the right group assembled, then the same edit in the candidate
    candidate, _receipt = assemble_candidate(HEADER, plan, [_grouped(_fragment(plan, context, 0)), _fragment(plan, context, 1)])
    _break(edit, *candidate["test_cases"][:2])
    assert _group_rows(validate_canonical_document_all(candidate)) == _group_rows(rows)


@pytest.mark.parametrize("edit", sorted(EXPECTED))
def test_assemble_still_refuses_a_group_error_with_its_code(edit: str) -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate

    plan, context = _split()
    with pytest.raises(BatchAssemblyError) as refused:
        assemble_candidate(HEADER, plan, [_grouped(_fragment(plan, context, 0), edit), _fragment(plan, context, 1)])
    assert refused.value.code == "BATCH_SEMANTIC_CONFLICT" and EXPECTED[edit] in {row["code"] for row in refused.value.diagnostics}


def test_a_right_group_in_each_batch_passes_validate_fragment_and_assembles() -> None:
    from tools.batch_assembly import assemble_candidate, validate_fragment
    from tools.canonical_document import param_groups

    plan, context = _split()
    first = _grouped(_fragment(plan, context, 0))
    second = _grouped(_fragment(plan, context, 1), group="PG-refund-amount")
    assert validate_fragment(copy.deepcopy(first), plan, HEADER, context) is None
    assert validate_fragment(copy.deepcopy(second), plan, HEADER, context, [first]) is None
    candidate, _receipt = assemble_candidate(HEADER, plan, [first, second])
    assert [(group["group_id"], group["parameter"]["id"], len(group["cases"])) for group in param_groups(candidate)] == [
        ("PG-amount", "P-amount", 2), ("PG-refund-amount", "P-amount", 2)]


def test_a_group_name_an_earlier_batch_took_is_refused_at_the_later_batch() -> None:
    """One name in two batches is one group of the document, and its cases cannot share requirement_ids: the audit refuses
    it (SEMANTIC_PARAM_GROUP_MISMATCH). Batch 2 is refused with that code before its answer is written; its fix is a name."""
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.canonical_document import validate_canonical_document_all

    plan, context = _split()
    first = _grouped(_fragment(plan, context, 0))
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(_grouped(_fragment(plan, context, 1)), plan, HEADER, context, [first])
    rows = refused.value.diagnostics
    assert [(row["path"], row["code"]) for row in rows] == [("/test_cases/0/param_group", "SEMANTIC_PARAM_GROUP_MISMATCH")], rows
    assert "PG-amount" in rows[0]["message"] and first["namespace"] in rows[0]["message"] and "PG-" in rows[0]["fix"], rows

    # what the audit says of the document with one name in both batches
    candidate, _receipt = assemble_candidate(HEADER, plan, [first, _grouped(_fragment(plan, context, 1), group="PG-refund-amount")])
    for case in candidate["test_cases"]:
        case["param_group"] = "PG-amount"
    assert "SEMANTIC_PARAM_GROUP_MISMATCH" in {row["code"] for row in validate_canonical_document_all(candidate)}


def test_a_group_error_is_refused_by_model_response_before_the_write_and_the_retry_assembles(tmp_path: Path) -> None:
    """The Petclinic path through `python -m tools.api`: the group error of batch 1 is refused by
    model-response before the write; the corrected answer goes as the one retry, batch 2 follows, assemble builds the group."""
    run = _generated_run(tmp_path, generated=False, header=HEADER)
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    broken = _put(run["tmp"] / "broken.json", _grouped(fragments[0], "undeclared"))
    fixed = _put(run["tmp"] / "grouped.json", _grouped(fragments[0]))

    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    before = _journal(run["run_root"])
    code, refused = _response(run, first, broken)
    assert code == 2 and refused["code"] == "BATCH_SEMANTIC_CONFLICT" and "--transport-attempts 2" in refused["message"], refused
    assert [row["code"] for row in refused["diagnostics"]] == ["SEMANTIC_PARAM_GROUP_PARAMETER"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))
    code, answered = _response(run, first, fixed, "--transport-attempts", "2")
    assert code == 0, answered

    code, prior = _prior(run, second)
    assert code == 0, prior
    assert _second_request(run, second, plan_digest, header_digest, prior["result"]["digest"])[0] == 0
    assert _response(run, second, run["fragments"][1])[0] == 0
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate = journal_assembly(run["run_root"], run["attempt_id"])[0]
    assert [case.get("param_group") for case in candidate["test_cases"]] == ["PG-amount", "PG-amount", None]


# --- the rules of the whole document at the last batch -------------------------------------------

def _without_russian(value):
    if isinstance(value, dict):
        return {key: _without_russian(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_russian(item) for item in value]
    return "Plain prose" if isinstance(value, str) and re.search(r"[А-Яа-яЁё]", value) else value


def _plain(fragment: dict) -> dict:
    from tools.batch_assembly import with_digest

    return with_digest(_without_russian({key: value for key, value in fragment.items() if key != "digest"}))


def _plain_split() -> tuple[dict, dict, dict, list[dict]]:
    """A ru-RU plan of two batches with no Russian in the header, the source requirements and the answers."""
    context = _context_receipt()
    header = _without_russian(_header())
    plan = _plan(header, context, _without_russian(_requirements()))
    return plan, header, context, [_plain(_fragment(plan, context, index)) for index in range(len(plan["batches"]))]


def test_the_last_batch_is_refused_a_rule_of_the_whole_document_and_one_russian_batch_is_enough() -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment

    plan, header, context, fragments = _plain_split()
    assert len(plan["batches"]) == 2
    assert validate_fragment(copy.deepcopy(fragments[0]), plan, header, context) is None  # batch 1: another batch may have it
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(copy.deepcopy(fragments[1]), plan, header, context, fragments[:1])
    rows = refused.value.diagnostics
    assert [(row["path"], row["code"]) for row in rows] == [("/content_locale", "SEMANTIC_RU_RU_HUMAN_FIELDS")] and rows[0]["fix"], rows

    russian = _fragment(plan, context, 1)  # the same last batch with its human fields in Russian
    assert validate_fragment(copy.deepcopy(russian), plan, header, context, fragments[:1]) is None
    candidate, _receipt = assemble_candidate(header, plan, [fragments[0], russian])
    assert len(candidate["test_cases"]) == 2


def test_the_last_batch_without_russian_is_refused_by_model_response_before_the_write_and_the_retry_assembles(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False, header=_without_russian(_header()), requirements=_without_russian(_requirements()))
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    plain = [_put(run["tmp"] / f"plain-{index}.json", _plain(fragment)) for index, fragment in enumerate(fragments)]

    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    assert _response(run, first, plain[0])[0] == 0
    code, prior = _prior(run, second)
    assert code == 0, prior
    assert _second_request(run, second, plan_digest, header_digest, prior["result"]["digest"])[0] == 0
    before = _journal(run["run_root"])
    code, refused = _response(run, second, plain[1])
    assert code == 2 and refused["code"] == "BATCH_SEMANTIC_CONFLICT" and "--transport-attempts 2" in refused["message"], refused
    assert [row["code"] for row in refused["diagnostics"]] == ["SEMANTIC_RU_RU_HUMAN_FIELDS"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))
    code, answered = _response(run, second, run["fragments"][1], "--transport-attempts", "2")
    assert code == 0, answered
    from tools.orchestrate_test_case_revision import journal_assembly

    assert journal_assembly(run["run_root"], run["attempt_id"])[0]["test_cases"]  # the journal's answers assemble
