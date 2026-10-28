"""FaT-HM: the remainder entry of capped hints — «и ещё N (дизайн|ожидания)» — passes the assembly receipt schema
(run-two-repos 79e14cc2…: 114 cases, hints over the cap, assemble refused SCHEMA_ONE_OF)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _receipt_with(warnings: list[dict]) -> dict:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan
    from tools.batch_assembly import assemble_candidate, with_digest

    context = _context_receipt()
    header = _header()
    plan = _plan(header, context)
    _candidate, receipt = assemble_candidate(header, plan, [_fragment(plan, context, 0), _fragment(plan, context, 1)])
    return with_digest({**{key: value for key, value in receipt.items() if key != "digest"}, "warnings": warnings})


def test_capped_hints_of_both_groups_over_the_cap_pass_the_receipt_schema() -> None:
    from tools.schema_validation import schema_diagnostics
    from tools.test_design_hints import _hint, capped_code_hints

    hints = [{"code": "TD_VAGUE_EXPECTATION", "case_id": f"TC-B1-{index:03d}", "step_id": f"STEP-B1-{index:03d}-1",
              "detail": "только status = 200"} for index in range(60)]
    hints += [_hint("TD_NEGATIVE_MISSING", f"CREQ-B1-{index:03d}", None, "нет негативного кейса") for index in range(60)]
    capped = capped_code_hints(hints)
    more = [row["detail"] for row in capped if row["code"] == "TD_MORE"]
    assert any(detail.endswith("(ожидания)") for detail in more) and any(detail.endswith("(дизайн)") for detail in more), more
    receipt = _receipt_with(capped)
    assert schema_diagnostics(receipt, ROOT / "schemas" / "assembly-receipt.schema.json", ROOT) == []


def test_an_unknown_group_label_is_still_refused_by_the_schema() -> None:
    from tools.schema_validation import schema_diagnostics

    receipt = _receipt_with([{"code": "TD_MORE", "detail": "и ещё 5 (прочее)"}])
    assert schema_diagnostics(receipt, ROOT / "schemas" / "assembly-receipt.schema.json", ROOT) != []
