"""FaT-O3 (audit 2): ``context_marker_parts merge`` builds and checks the merged marker before it writes the request of
``context-marker:baseline`` — a refusal leaves no orphan request, names its own code and no ``--transport-attempts``."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_marker_parts import _marker, _publish_part, _publish_requirements, _run
from tools import pilot_state


def _baseline_requests(root: Path) -> list[dict]:
    return [event for event in pilot_state.derive_state(root)["events"]
            if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == "context-marker:baseline"]


def test_a_refused_merge_writes_no_request_and_the_merge_goes_on_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.context_marker_parts import merge_parts
    from tools.json_cli import error_fields

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — x"]))

    original = pilot_state._validate_context_marker_sources

    def refuse(*args):
        if args[4]["stage_instance_id"] != "context-marker:baseline":
            return original(*args)
        raise ValueError("CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: []; fix: повтор (--transport-attempts 2)")

    monkeypatch.setattr(pilot_state, "_validate_context_marker_sources", refuse)
    with pytest.raises(ValueError) as refused:
        merge_parts(root)
    fields = error_fields(refused.value)
    assert fields["code"] == "CONTEXT_MARKER_MERGE_INVALID" and "CONTEXT_MARKER_OPENSPEC" in fields["message"], fields
    assert "--transport-attempts" not in fields["fix"] and "scan --new-run" in fields["fix"], fields
    assert _baseline_requests(root) == []
    monkeypatch.undo()
    assert merge_parts(root)["content_digest"] and len(_baseline_requests(root)) == 1  # nothing orphaned: the merge goes on
