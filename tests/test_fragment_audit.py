"""FaT-VF: validate_fragment runs the canonical pre-review audit of assemble on one batch, so an order or format error is
refused before the answer is written (model-response), not first by assemble of all batches."""
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


def test_the_doc_only_batch_1_answer_is_refused_with_the_code_assemble_gave(tmp_path: Path) -> None:
    """run-doc-only c1f13463…: validate-fragment passed batch 1, assemble of both batches refused CANONICAL_CATEGORY_ORDER."""
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    fragment, plan, header = _load("doc-only-fragment-1.json"), _load("doc-only-plan.json"), _load("doc-only-header.json")
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(fragment, plan, header, fragment["context_receipt"])
    assert refused.value.code == "BATCH_SEMANTIC_CONFLICT" and "pre-review audit failed: CANONICAL_CATEGORY_ORDER" in str(refused.value)
    validate_fragment(_fixed(fragment), plan, header, fragment["context_receipt"])  # the corrected answer goes


def _synthetic() -> tuple[dict, dict, dict, dict]:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan

    context = _context_receipt()
    header = _header()
    plan = _plan(header, context)
    return _fragment(plan, context, 0), plan, header, context


@pytest.mark.parametrize("broken", ["categories", "requirement_ids"])
def test_validate_fragment_and_the_command_refuse_an_order_error_assemble_refuses_the_same(broken: str, tmp_path: Path, capsys) -> None:
    from tests.test_batch_assembly import _fragment
    from tools import api
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment

    fragment, plan, header, context = _synthetic()
    bad = copy.deepcopy(fragment)
    case = bad["test_cases"][0]
    if broken == "categories":
        case["categories"] = ["functional", "positive"]
        expected = "CANONICAL_CATEGORY_ORDER"
    else:
        second = dict(bad["requirements"][0], requirement_id=bad["requirements"][0]["requirement_id"] + "-B", display_order=100)
        bad["requirements"].append(second)
        bad["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
        case["requirement_ids"] = [second["requirement_id"], case["requirement_ids"][0]]
        expected = "CANONICAL_REQUIREMENT_ID_ORDER"
    bad = _resealed(bad)
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(bad, plan, header, context)
    assert refused.value.code == "BATCH_SEMANTIC_CONFLICT" and f"pre-review audit failed: {expected}" in str(refused.value)
    with pytest.raises(BatchAssemblyError) as assembled:
        assemble_candidate(header, plan, [bad, _fragment(plan, context, 1)])
    assert assembled.value.code == "BATCH_SEMANTIC_CONFLICT" and f"pre-review audit failed: {expected}" in str(assembled.value)
    paths = {name: tmp_path / f"{name}.json" for name in ("fragment", "plan", "header", "context")}
    for name, value in zip(paths, (bad, plan, header, context)):
        paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    code = api.main(["validate-fragment", "--fragment", str(paths["fragment"]), "--plan", str(paths["plan"]), "--header", str(paths["header"]),
                     "--context-receipt", str(paths["context"])])
    printed = json.loads(capsys.readouterr().out)
    assert code == 2 and printed["code"] == "BATCH_SEMANTIC_CONFLICT" and expected in printed["message"], printed


def test_a_valid_split_still_assembles_and_document_wide_rules_stay_in_assemble() -> None:
    from tests.test_batch_assembly import _fragment
    from tools.batch_assembly import DOCUMENT_WIDE_CODES, assemble_candidate, validate_fragment

    fragment, plan, header, context = _synthetic()
    validate_fragment(fragment, plan, header, context)
    candidate, _receipt = assemble_candidate(header, plan, [fragment, _fragment(plan, context, 1)])
    assert len(candidate["test_cases"]) == 2
    source = (Path(__file__).resolve().parents[1] / "tools" / "canonical_document.py").read_text(encoding="utf-8")
    assert all(f'"{code}"' in source for code in DOCUMENT_WIDE_CODES)  # each one a real code of the audit
