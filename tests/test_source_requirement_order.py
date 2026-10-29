"""FaT-SN: source requirements follow their documents — (document, line as a number). run-doc-only numbered them by the
string order of their references (`…md:12` < `…md:4`), so SCOPE (line 4) landed between P05 and P06 and the batches with it.
The marker writes the numbers; model-response now refuses an answer out of document order, and journals already written read
as before."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

DOC = "docs/portable-owner-lifecycle-requirements.md"


def _row(number: int, line: int, path: str = DOC) -> dict:
    return {"source_requirement_id": f"SREQ-{number:04d}", "display_order": number, "text": f"line {line}",
            "provenance": [f"{path}:{line} — R{line}; sha256:{'0' * 64}"], "digest": "sha256:" + "0" * 64}


def _value(rows: list[dict]) -> dict:
    return {"artifacts": {"analytics_documentation": {"requirements": rows}}}


def _order(rows: list[dict]) -> str | None:
    from tools.pilot_state import _requirement_order_rows

    return _requirement_order_rows("context-marker:requirements", _value(rows))


def test_line_4_comes_before_line_12() -> None:
    assert _order([_row(1, 4), _row(2, 12), _row(3, 39), _row(4, 41)]) is None


def test_the_string_order_of_references_is_refused() -> None:
    found = _order([_row(1, 12), _row(2, 39), _row(3, 4), _row(4, 41)])  # "…:12" < "…:39" < "…:4" < "…:41" as strings
    assert found is not None and f"{DOC}:4" in found


def test_two_documents_in_the_answer_s_own_order_pass() -> None:
    """The documents may come in --docs order or the task document first — not forced by their paths."""
    other = "docs/a-other.md"  # sorts before DOC by path
    assert _order([_row(1, 4), _row(2, 12), _row(3, 5, other), _row(4, 30, other)]) is None
    assert _order([_row(1, 5, other), _row(2, 30, other), _row(3, 4), _row(4, 12)]) is None


def test_documents_mixed_together_are_refused() -> None:
    other = "docs/a-other.md"
    found = _order([_row(1, 4), _row(2, 5, other), _row(3, 12)])
    assert found is not None and "идут не подряд" in found


def test_lines_must_grow_within_each_document() -> None:
    other = "docs/a-other.md"
    found = _order([_row(1, 4), _row(2, 12), _row(3, 30, other), _row(4, 5, other)])
    assert found is not None and f"{other}:30" in found


def test_sreq_numbers_follow_the_display_order() -> None:
    rows = [_row(1, 4), _row(2, 12)]
    rows[0]["source_requirement_id"], rows[1]["source_requirement_id"] = "SREQ-0002", "SREQ-0001"
    assert _order(rows) == "номера SREQ идут не в порядке display_order"


def test_sreq_numbers_compare_as_numbers() -> None:
    rows = [_row(1, 4), _row(2, 12)]
    rows[0]["source_requirement_id"], rows[1]["source_requirement_id"] = "SREQ-9", "SREQ-10"  # "SREQ-10" < "SREQ-9" as strings
    assert _order(rows) is None
    rows[0]["source_requirement_id"], rows[1]["source_requirement_id"] = "SREQ-10", "SREQ-9"
    assert _order(rows) == "номера SREQ идут не в порядке display_order"
    rows[0]["source_requirement_id"] = "SREQ-scope"  # no number at the end: the numbering is not checked
    assert _order(rows) is None


def test_rows_without_a_line_reference_keep_the_model_s_order() -> None:
    rows = [_row(1, 12), _row(2, 4)]
    rows[0]["provenance"] = ["docs — requirement without a line"]
    assert _order(rows) is None
    from tools.pilot_state import _requirement_order_rows

    assert _requirement_order_rows("tc-generator:BATCH-a", _value([_row(1, 12), _row(2, 4)])) is None


def _published(tmp_path: Path, rows: list[dict], monkeypatch: pytest.MonkeyPatch | None = None):
    from tests.test_marker_parts import _marker, _run
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER
    from tools import pilot_state

    root, attempt_id, baseline, _receipts = _run(tmp_path, files=1)
    pilot_state.publish_model_request(root, attempt_id, "context-marker:requirements", model_id="model-context", invocation_id="context-req",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]])
    marker = _marker([REQUIREMENTS_PLACEHOLDER], requirements=rows)
    if monkeypatch is None:
        return root, attempt_id, pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", marker)
    with monkeypatch.context() as old:  # the package before FaT-SN: no order check at model-response
        old.setattr(pilot_state, "_requirement_order_rows", lambda *_args: None)
        return root, attempt_id, pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", marker)


def test_model_response_refuses_an_answer_out_of_order_and_writes_nothing(tmp_path: Path) -> None:
    from tests.test_marker_parts import _requirements
    from tools import pilot_state

    rows = copy.deepcopy(_requirements())
    if len(rows) < 2:
        rows.append(dict(copy.deepcopy(rows[0]), source_requirement_id="SREQ-9998", display_order=rows[0]["display_order"] + 1))
    rows[0]["provenance"] = [f"{DOC}:12 — first"] + list(rows[0]["provenance"])[1:]
    rows[1]["provenance"] = [f"{DOC}:4 — second"] + list(rows[1]["provenance"])[1:]
    with pytest.raises(ValueError, match="SOURCE_REQUIREMENT_ORDER") as refused:
        _published(tmp_path, rows)
    assert "--transport-attempts 2" in str(refused.value)
    [root] = list((tmp_path / ".pilot-runs").iterdir())
    events = pilot_state.derive_state(root)["events"]
    assert events[-1]["event_type"] == "MODEL_REQUESTED"


def test_a_journal_written_before_the_check_reads_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_marker_parts import _requirements
    from tools import pilot_state

    rows = copy.deepcopy(_requirements())
    if len(rows) < 2:
        rows.append(dict(copy.deepcopy(rows[0]), source_requirement_id="SREQ-9998", display_order=rows[0]["display_order"] + 1))
    rows[0]["provenance"] = [f"{DOC}:12 — first"] + list(rows[0]["provenance"])[1:]
    rows[1]["provenance"] = [f"{DOC}:4 — second"] + list(rows[1]["provenance"])[1:]
    root, attempt_id, published = _published(tmp_path, rows, monkeypatch)
    read = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", published["content_digest"])
    assert read["artifact"]["artifacts"]["analytics_documentation"]["requirements"] == rows  # same bytes, same IDs
    assert pilot_state.derive_state(root)["events"][-1]["event_type"] == "MODEL_RESPONSE_RECEIVED"
