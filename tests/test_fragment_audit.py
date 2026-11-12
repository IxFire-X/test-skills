"""FaT-VF: validate_fragment runs the canonical pre-review audit of assemble on one batch, so an order or format error is
refused before the answer is written (submit), not first by assemble of all batches."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fat-vf"
PRECEDENCE = ["positive", "negative", "boundary", "authorization", "security", "concurrency", "idempotency", "observability", "functional"]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _resealed(fragment: dict) -> dict:
    from tools.batch_assembly import with_digest

    return with_digest({key: value for key, value in fragment.items() if key != "digest"})


def _fixed(fragment: dict) -> dict:
    """The doc-only answer with categories in V3 precedence and requirement_ids in requirement order."""
    order = [row["requirement_id"] for row in fragment["requirements"]]
    fixed = copy.deepcopy(fragment)
    for case in fixed["test_cases"]:
        case["categories"] = sorted(case["categories"], key=PRECEDENCE.index)
        case["requirement_ids"] = sorted(case["requirement_ids"], key=order.index)
    return _resealed(fixed)


def test_the_doc_only_batch_1_answer_is_put_in_order_by_the_code(tmp_path: Path) -> None:
    """run-doc-only c1f13463…: the check of the fragment passed batch 1, assemble of both batches refused CANONICAL_CATEGORY_ORDER.
    P1: the order is the code's work — the check of a fragment (and submit) put the categories in V3 order and say so; the rule
    stays in the document audit; P1-fix2: assemble puts the same file in the same order."""
    from tools.batch_assembly import normalize_fragment, validate_fragment

    fragment, plan, header = _load("doc-only-fragment-1.json"), _load("doc-only-plan.json"), _load("doc-only-header.json")
    fragment.pop("digest", None)
    result = validate_fragment(fragment, plan, header, fragment["context_receipt"])
    assert result and any(row.endswith("/categories") for row in result["normalized"]), result
    done, _names = normalize_fragment(fragment)
    assert [case["categories"] for case in done["test_cases"]] == [sorted(case["categories"], key=PRECEDENCE.index) for case in fragment["test_cases"]]
    validate_fragment(_fixed(fragment), plan, header, fragment["context_receipt"])  # the corrected answer goes as before


def _synthetic() -> tuple[dict, dict, dict, dict]:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan

    context = _context_receipt()
    header = _header()
    plan = _plan(header, context)
    return _fragment(plan, context, 0), plan, header, context


@pytest.mark.parametrize("broken", ["categories", "requirement_ids"])
def test_validate_fragment_puts_an_order_error_in_order_and_assemble_takes_the_same_file(broken: str) -> None:
    from tests.test_batch_assembly import _fragment
    from tools.batch_assembly import assemble_candidate, validate_fragment

    fragment, plan, header, context = _synthetic()
    bad = copy.deepcopy(fragment)
    case = bad["test_cases"][0]
    if broken == "categories":
        case["categories"] = ["functional", "positive"]
    else:
        second = dict(bad["requirements"][0], requirement_id=bad["requirements"][0]["requirement_id"] + "-B", display_order=100)
        bad["requirements"].append(second)
        bad["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
        case["requirement_ids"] = [second["requirement_id"], case["requirement_ids"][0]]
    bad.pop("digest", None)  # a digest sealed over the unordered content would be refused: «удали поле digest»
    result = validate_fragment(bad, plan, header, context)  # P1: put in order by the code, named
    assert result == {"normalized": [f"/test_cases/0/{broken}"]}, result
    candidate, _receipt = assemble_candidate(header, plan, [bad, _fragment(plan, context, 1)])  # P1-fix2: the same canon, from the same file
    assert candidate["test_cases"][0][broken] == (["positive", "functional"] if broken == "categories" else sorted(case["requirement_ids"]))


def test_a_valid_split_still_assembles_and_document_wide_rules_stay_in_assemble() -> None:
    from tests.test_batch_assembly import _fragment
    from tools.batch_assembly import DOCUMENT_WIDE_CODES, assemble_candidate, validate_fragment

    fragment, plan, header, context = _synthetic()
    validate_fragment(fragment, plan, header, context)
    candidate, _receipt = assemble_candidate(header, plan, [fragment, _fragment(plan, context, 1)])
    assert len(candidate["test_cases"]) == 2
    source = (Path(__file__).resolve().parents[1] / "tools" / "canonical_document.py").read_text(encoding="utf-8")
    assert all(f'"{code}"' in source for code in DOCUMENT_WIDE_CODES)  # each one a real code of the audit


@pytest.mark.parametrize("broken", ["categories", "requirement_ids"])
def test_the_audit_of_the_canon_still_catches_a_wrong_order_in_an_assembled_candidate(broken: str) -> None:
    """P1-fix2: assemble puts the order in itself, so the one thing that catches a hand-edited candidate with a wrong order
    is the audit validate-canonical runs — here on a candidate assembled right and then turned around."""
    from tests.test_batch_assembly import _fragment
    from tools.batch_assembly import assemble_candidate
    from tools.canonical_document import validate_canonical_document_all

    fragment, plan, header, context = _synthetic()
    case = fragment["test_cases"][0]
    second = dict(fragment["requirements"][0], requirement_id=fragment["requirements"][0]["requirement_id"] + "-B", display_order=100)
    fragment["requirements"].append(second)
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
    case["requirement_ids"] = [fragment["requirements"][0]["requirement_id"], second["requirement_id"]]
    case["categories"] = ["positive", "functional"]
    fragment.pop("digest", None)
    candidate, _receipt = assemble_candidate(header, plan, [fragment, _fragment(plan, context, 1)])
    assert validate_canonical_document_all(candidate) == []
    candidate["test_cases"][0][broken].reverse()
    expected = "CANONICAL_CATEGORY_ORDER" if broken == "categories" else "CANONICAL_REQUIREMENT_ID_ORDER"
    assert expected in {row["code"] for row in validate_canonical_document_all(candidate)}
