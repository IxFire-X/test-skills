"""FaT-H4a (hunt 4.1): the plan reads a prefix, in batch_index order, of every context receipt of the attempt; a skipped or
misordered receipt is refused with the missing digest, and plan-batches says which grouping field is missing."""
from __future__ import annotations

import pytest

from tests.test_context_batches import _generator_journal, _grouping, _requirements, three  # noqa: F401 — the fixture


def test_a_plan_that_skips_a_context_receipt_is_refused_with_the_skipped_digest(three) -> None:
    receipts = three[4]
    with pytest.raises(ValueError, match="GENERATOR_INPUT_CONTEXT") as refused:
        _generator_journal(three, [receipts[0], receipts[2]])
    message = str(refused.value)
    assert f"position 2 must be {receipts[1]['digest']} (batch_index {receipts[1]['batch_index']})" in message, message
    assert "fix: put every digest in the grouping context_receipt_digests" in message


def test_a_plan_that_starts_after_the_first_context_receipt_is_refused(three) -> None:
    receipts = three[4]
    with pytest.raises(ValueError, match=f"position 1 must be {receipts[0]['digest']}"):
        _generator_journal(three, [receipts[1], receipts[0]])


def test_a_prefix_of_the_context_receipts_is_published(three) -> None:
    receipts = three[4]
    _root, _attempt, _marker, plan, _header = _generator_journal(three, receipts[:2])
    assert plan.startswith("sha256:")


def test_plan_batches_names_the_missing_grouping_field(three) -> None:
    from tools.batch_assembly import BatchAssemblyError, plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    with pytest.raises(BatchAssemblyError, match="does not match batch binding; fix: add context_receipt_digests to the grouping"):
        plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], receipts)


def test_publish_of_the_plan_says_which_context_batches_of_the_attempt_the_generator_sees(three) -> None:
    from tools import pilot_state
    from tools.batch_assembly import context_note

    receipts = three[4]
    _generator_journal(three, receipts[:2])
    root, attempt_id = three[1], three[2]
    plan = pilot_state._generator_input(root.parents[1], root, pilot_state.derive_state(root), attempt_id, "plan")
    again = pilot_state.publish_generator_input(root, attempt_id, "plan", plan)  # the same plan again: the same result
    assert again["context"] == context_note(receipts, 2) and "видит 2 из 3" in again["context"], again
