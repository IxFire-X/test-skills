"""V6a: the reviewer's blocking findings reject their cases, the code subtracts them (r2) and checks the equality; canaries do not reject.

Pure functions of ``tools.revision_selection`` over the example document (64 cases, almost every case owns one requirement):
the decision, the subtraction, the lineage check, the two validator clauses of a revision-2 document and the guards that keep a
forged revision-2 candidate out of every agent-fed input."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"


def r1_document() -> dict:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["revision"], document["parent_sha256"] = 1, None
    return document


def _ids(document: dict, number: int) -> dict:
    case = document["test_cases"][number - 1]
    step = case["steps"][0]
    return {"case": case["case_id"], "step": step["step_id"], "exp": step["expectations"][0]["expectation_id"]}


def _canary(document: dict, number: int, marker: str) -> dict:
    ids = _ids(document, number)
    return {"canary_id": f"CAN-{number}", "class": "boundary", "case_id": ids["case"], "step_id": ids["step"], "expectation_id": ids["exp"],
            "assertion_id": None, "field": "expectation", "original": "ok", "mutated": "ok " + marker, "markers": [marker]}


def _finding(severity: str, related: list[str], message: str = "Замечание ревьюера.") -> dict:
    return {"severity": severity, "code": "REVIEW_NOTE", "message": message, "evidence": ["REQ"], "related_ids": related}


def _report(document: dict, *findings: dict) -> dict:
    from tools.canonical_document import document_sha256

    ref = {"document_id": document["document_id"], "revision": document["revision"], "document_sha256": document_sha256(document)}
    return {"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "candidate": ref, "effective": None, "corrections": [], "findings": list(findings),
            "reviewed_case_ids": [case["case_id"] for case in document["test_cases"]]}


def _real(document: dict, *numbers: int) -> list[dict]:
    return [_finding("BLOCKING", [document["test_cases"][number - 1]["case_id"]]) for number in numbers]


def _outcome(document: dict, findings: list[dict], canaries=(), share: int = 20) -> dict:
    from tools.revision_selection import review_outcome

    return review_outcome(document, _report(document, *findings), canaries, share)


# --- the decision ---------------------------------------------------------------------------------

def test_one_real_rejected_case_and_three_canaries_is_partial() -> None:
    document = r1_document()
    canaries = [_canary(document, 4, "7771"), _canary(document, 38, "7772"), _canary(document, 26, "7773")]
    findings = [_finding("BLOCKING", [_ids(document, 4)["step"], _ids(document, 4)["exp"]], "Названо 7771."),
                _finding("BLOCKING", [_ids(document, 38)["exp"]], "Названо 7772."),
                _finding("INFO", [_ids(document, 26)["exp"]], "Названо 7773."), *_real(document, 1)]
    outcome = _outcome(document, findings, canaries)
    assert (outcome["kind"], outcome["rejected"], outcome["with_group"]) == ("PARTIAL", ["TC-B1-001"], [])
    assert [case["case_id"] for case in outcome["effective"]["test_cases"]] == [case["case_id"] for case in document["test_cases"][1:]]


def test_blocking_findings_only_on_canaries_accept_the_whole_set() -> None:
    document = r1_document()
    findings = [_finding("BLOCKING", [_ids(document, 4)["exp"]], "Названо 7771.")]
    outcome = _outcome(document, findings, [_canary(document, 4, "7771")])
    assert outcome["kind"] == "ACCEPTED_WHOLE" and outcome["effective"] == document


def test_a_blocking_finding_without_a_case_rejects_the_whole_set() -> None:
    document = r1_document()
    outcome = _outcome(document, [*_real(document, 1), _finding("BLOCKING", ["CREQ-B1-002"])])
    assert (outcome["kind"], outcome["reason"], outcome["effective"]) == ("REWORK", "NO_CASES", None)


def test_a_rework_answer_without_a_blocking_finding_is_the_whole_set_not_an_acceptance() -> None:
    document = r1_document()
    outcome = _outcome(document, [_finding("WARNING", [document["test_cases"][0]["case_id"]])])
    assert (outcome["kind"], outcome["reason"], outcome["effective"]) == ("REWORK", "NO_BLOCKING", None)


def test_a_step_id_rejects_the_case_that_owns_it() -> None:
    document = r1_document()
    outcome = _outcome(document, [_finding("BLOCKING", [_ids(document, 7)["step"]])])
    assert outcome["rejected"] == ["TC-B1-007"]


def test_a_finding_that_names_a_canary_and_a_real_case_is_not_a_canary_finding() -> None:
    document = r1_document()
    findings = [_finding("BLOCKING", [_ids(document, 4)["exp"], "TC-B1-009"], "Названо 7771.")]
    outcome = _outcome(document, findings, [_canary(document, 4, "7771")])
    assert outcome["rejected"] == ["TC-B1-004", "TC-B1-009"]


@pytest.mark.parametrize("count, share, kind", [(12, 20, "PARTIAL"), (13, 20, "REWORK"), (16, 25, "PARTIAL"), (17, 25, "REWORK")])
def test_the_share_of_removed_cases_is_a_boundary(count: int, share: int, kind: str) -> None:
    document = r1_document()
    outcome = _outcome(document, _real(document, *range(1, count + 1)), share=share)
    assert outcome["kind"] == kind and (kind == "PARTIAL" or outcome["reason"] == "OVER_SHARE")


def _grouped() -> dict:
    from tests.test_param_groups import grouped

    document = grouped()
    document["revision"], document["parent_sha256"] = 1, None
    return document


def _group_ids(document: dict) -> list[str]:
    return [case["case_id"] for case in document["test_cases"] if "param_group" in case]


def test_a_group_keeps_its_two_remaining_variants() -> None:
    document = _grouped()
    outcome = _outcome(document, [_finding("BLOCKING", [_group_ids(document)[2]])])
    assert (outcome["rejected"], outcome["with_group"]) == ([_group_ids(document)[2]], [])


def test_a_group_with_one_variant_left_goes_out_whole() -> None:
    document = _grouped()
    ids = _group_ids(document)
    outcome = _outcome(document, [_finding("BLOCKING", ids[:2])])
    assert (outcome["rejected"], outcome["with_group"]) == (ids[:2], ids[2:])
    assert outcome["removed"] == 3


def test_a_case_dragged_out_with_its_group_counts_for_the_share() -> None:
    document = _grouped()  # 66 cases: 13 removed is over 20 %, a group of three makes it 12 + 3
    ids = _group_ids(document)
    others = [case["case_id"] for case in document["test_cases"] if "param_group" not in case][:11]
    assert _outcome(document, [_finding("BLOCKING", others + ids[:1])])["kind"] == "PARTIAL"
    assert _outcome(document, [_finding("BLOCKING", others + ids[:2])])["reason"] == "OVER_SHARE"


# --- the subtraction ------------------------------------------------------------------------------

def test_the_subtraction_is_valid_and_names_the_requirements_it_left_without_a_case() -> None:
    from tools.canonical_document import validate_canonical_document
    from tools.revision_selection import check_subtraction, coverage_gaps

    document = r1_document()
    effective = _outcome(document, _real(document, 1))["effective"]
    assert validate_canonical_document(effective) == [] and (effective["revision"], effective["test_cases"][0]["display_order"]) == (2, 2)
    assert check_subtraction(document, effective, ["TC-B1-001"]) == []
    assert coverage_gaps(effective) == ["CREQ-B1-008", "CREQ-B1-009", "CREQ-B1-024"]


def test_a_kept_case_that_differs_by_one_byte_is_refused() -> None:
    from tools.revision_selection import check_subtraction

    document = r1_document()
    effective = _outcome(document, _real(document, 1))["effective"]
    effective["test_cases"][3]["title"] += "."
    assert check_subtraction(document, effective, ["TC-B1-001"])[0]["code"] == "SUBTRACTION_CASE_CHANGED"


def test_a_case_taken_out_that_was_not_rejected_is_refused() -> None:
    from tools.revision_selection import check_subtraction

    document = r1_document()
    effective = _outcome(document, _real(document, 1, 2))["effective"]
    assert check_subtraction(document, effective, ["TC-B1-001"])[0]["code"] == "SUBTRACTION_REMOVED_SET"


def test_a_rejected_case_put_back_is_refused() -> None:
    from tools.revision_selection import check_subtraction

    document = r1_document()
    effective = _outcome(document, _real(document, 1))["effective"]
    assert check_subtraction(document, effective, ["TC-B1-001", "TC-B1-002"])[0]["code"] == "SUBTRACTION_REMOVED_SET"


def test_the_subtraction_changes_nothing_but_the_cases_and_the_lineage() -> None:
    from tools.revision_selection import check_subtraction

    document = r1_document()
    effective = _outcome(document, _real(document, 1))["effective"]
    effective["requirements"][0]["text"] += "."
    assert check_subtraction(document, effective, ["TC-B1-001"])[0]["code"] == "SUBTRACTION_DOCUMENT_CHANGED"


def test_the_binding_of_a_partial_effective_needs_exactly_the_rejected_set() -> None:
    from tools.revision_selection import partial_is_bound

    document = r1_document()
    report = _report(document, *_real(document, 1))
    effective = _outcome(document, _real(document, 1))["effective"]
    assert partial_is_bound(document, report, (), effective)
    assert not partial_is_bound(document, report, (), _outcome(document, _real(document, 1, 2))["effective"])
    assert not partial_is_bound(document, _report(document, *_real(document, 1, 2)), (), effective)


# --- the validator clauses and the guards ---------------------------------------------------------

def test_a_revision_one_document_keeps_the_contiguous_order_and_the_coverage_rule() -> None:
    from tools.canonical_document import validate_canonical_document

    document = r1_document()
    del document["test_cases"][0]
    assert {row["code"] for row in validate_canonical_document(document)} >= {"CANONICAL_DISPLAY_ORDER"}
    document["test_cases"][0]["display_order"] = 1
    for case in document["test_cases"]:
        case["display_order"] = document["test_cases"].index(case) + 1
    assert {row["code"] for row in validate_canonical_document(document)} == {"SEMANTIC_UNCOVERED_REQUIREMENT"}


def _forged(gap: str) -> dict:
    """A candidate of an agent that claims revision 2 and a parent: with a requirement no case covers, or a hole in display_order."""
    from tools.canonical_document import document_sha256

    document = r1_document()
    parent = document_sha256(document)
    del document["test_cases"][0 if gap == "uncovered" else 5]
    if gap == "uncovered":
        for index, case in enumerate(document["test_cases"], 1):
            case["display_order"] = index
    document["revision"], document["parent_sha256"] = 2, parent
    return document


@pytest.mark.parametrize("gap", ["uncovered", "order"])
def test_the_forged_candidate_passes_the_validator_but_no_candidate_input(gap: str) -> None:
    from tools.canonical_document import validate_canonical_document
    from tools.revision_selection import candidate_rows

    forged = _forged(gap)
    assert validate_canonical_document(forged) == []  # the clause is by content: the guards are the inputs'
    assert candidate_rows(forged)[0]["code"] == "CANDIDATE_REVISION"
    assert candidate_rows(r1_document()) == []


def test_the_validate_canonical_command_refuses_a_candidate_that_is_not_revision_one(tmp_path: Path) -> None:
    from tools.api import _cmd_validate_canonical

    assert _cmd_validate_canonical(type("Args", (), {"document": _forged("uncovered")})())[0]["code"] == "CANDIDATE_REVISION"


def test_the_header_of_assemble_cannot_claim_a_revision() -> None:
    from tools.batch_assembly import BatchAssemblyError, _document

    with pytest.raises(BatchAssemblyError, match="revision"):
        _document({"revision": 2, "parent_sha256": "sha256:" + "a" * 64}, {"source_requirements": []}, [])


def test_an_auto_fix_successor_cannot_renumber_a_case_or_add_a_requirement() -> None:
    from tools.canonical_document import document_sha256
    from tools.revision_selection import validate_successor

    document = r1_document()
    successor = copy.deepcopy(document)
    successor["revision"], successor["parent_sha256"] = 2, document_sha256(document)
    assert validate_successor(document, successor) == []
    successor["test_cases"][-1]["display_order"] = 70
    assert validate_successor(document, successor)[0]["code"] == "TRACEABILITY_CHANGED"
    successor["test_cases"][-1]["display_order"] = 64
    successor["requirements"].append(copy.deepcopy(successor["requirements"][-1]) | {"requirement_id": "CREQ-B1-999", "display_order": 79})
    assert validate_successor(document, successor)[0]["code"] == "TRACEABILITY_CHANGED"


def _event(report: dict) -> dict:
    import hashlib

    from tools.canonical_document import canonical_bytes

    return {"event_type": "AUTHORITATIVE_VERDICT", "verdict": "ACCEPTED", "report_digest": "sha256:" + hashlib.sha256(canonical_bytes(report)).hexdigest(),
            "verdict_digest": "sha256:" + hashlib.sha256(canonical_bytes({"verdict": report["verdict"], "effective": report["effective"]})).hexdigest()}


def test_a_ledger_that_accepts_an_answer_the_code_sends_back_whole_binds_no_effective() -> None:
    """The read side of the ledger event: ACCEPTED beside a report with a case-less blocking finding is bound to no effective document (r1 or r2)."""
    from tools.canonical_document import document_sha256
    from tools.revision_selection import effective_review_is_bound

    document = r1_document()
    digest, subtracted = document_sha256(document), _outcome(document, _real(document, 1))["effective"]
    whole = _report(document, *_real(document, 1), _finding("BLOCKING", ["CREQ-B1-002"]))
    for effective in (document, subtracted):
        assert not effective_review_is_bound(digest, effective, whole, None, _event(whole), document, [])
    partial = _report(document, *_real(document, 1))
    assert effective_review_is_bound(digest, subtracted, partial, None, _event(partial), document, [])
