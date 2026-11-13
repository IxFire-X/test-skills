"""P2 on the cycle of the agent: the review of the cases is ``prepare --stage tc-reviewer:canonical`` then the answer in its slot and ``submit``.

- ``prepare`` publishes an unpublished candidate itself and leaves the events of the review (candidate, request, model request), the boundary, the ledger;
- ``submit`` records the answer, completes the session and selects; the stage keeps its one retry (``--transport-attempts 2``);
- a ``submit`` without a slot, after the attempt is closed, a wrong answer: refused with its code, nothing written;
- ``submit --dry-run`` runs every check of the verdict and writes nothing, byte for byte, refusals included.

The helpers ``_open`` and ``_select_verdict`` are the cycle for the tests of other files (the answer of ``tests.test_review_requested._review_answer`` goes to the slot)."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run, _journal, _put, _review_answer
from tests.journal_helpers import assert_refused_once

STAGE = "tc-reviewer:canonical"


def _flags(run: dict) -> list[str]:
    return ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]


def _bundles(run: dict) -> str:
    return str(run["run_root"] / "published")


def _cycle(command: str, run: dict, *args: str) -> tuple[int, dict]:
    from tools import api

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = api.main([command, "--run-root", str(run["run_root"]), "--stage", STAGE, *args])
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def _open(run: dict, *extra: str, fresh: str | None = "yes", invocation: str = "reviewer-subagent-1") -> tuple[int, dict]:
    fresh_flags = ["--fresh-context", fresh] if fresh is not None else []
    return _cycle("prepare", run, "--invocation-id", invocation, *fresh_flags, *extra)


def _candidate_file(run: dict) -> None:
    """The file ``_review_answer`` reads the candidate from (the cycle writes none itself)."""
    from tools.orchestrate_test_case_revision import journal_assembly

    _put(run["tmp"] / "candidate.json", journal_assembly(run["run_root"], run["attempt_id"])[0])


def _verdict(run: dict, verdict: str = "ПРИНЯТО") -> str:
    _candidate_file(run)
    return _review_answer(run, verdict)


def _slot(run: dict, verdict: str) -> None:
    from tools import stage_steps

    slot = stage_steps.slot(run["run_root"], STAGE)
    slot.parent.mkdir(exist_ok=True)
    slot.write_bytes(Path(verdict).read_bytes())


def _select_verdict(run: dict, verdict: str, *extra: str) -> tuple[int, dict]:
    """The answer file into the slot, then ``submit``."""
    _slot(run, verdict)
    return _cycle("submit", run, *extra)


def _events(run_root: Path) -> list[dict]:
    return [json.loads(line) for line in _journal(run_root).decode("utf-8").splitlines()]


def _shape(run_root: Path, without: str | None = None) -> list[tuple]:
    """Event type, stage and batch of every event; the batch ids of two runs differ, so they are numbered in order of appearance.
    ``without`` — the digest of a receipt whose event pair is left out (P7: ``prepare`` binds the canary receipt)."""
    batches: dict[str, int] = {}

    def name(value):
        if value is None or value == "reviewer-ledger-v1":
            return value
        return f"batch-{batches.setdefault(str(value).removeprefix('tc-generator:'), len(batches) + 1)}"

    return [(row["event_type"], name(row.get("stage_instance_id")) if str(row.get("stage_instance_id")).startswith("tc-generator:") else row.get("stage_instance_id"),
             name(row.get("batch_id"))) for row in _events(run_root)
            if row["event_type"] != "COMMAND_REFUSED" and (without is None or row.get("artifact_digest") != without)]  # J1: a refusal is a run-level event of its own, stated by the tests that cause one


def _tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _ledger(run: dict) -> dict:
    from tools import pilot_state

    return pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])


def _boundary(run: dict) -> dict:
    code, read = _api("read-receipt", *_flags(run), "--kind", "reviewer-session-boundary", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, read
    return read["result"]["record"]


# --------------------------------------------------------------------------------------
# prepare: the events of the review, the unpublished candidate
# --------------------------------------------------------------------------------------

def test_prepare_and_submit_leave_the_events_of_the_review(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert not any(row[1] == "assembly" for row in _shape(run["run_root"]))
    code, opened = _open(run)
    assert code == 0, opened
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected
    stages = [row for row in _shape(run["run_root"]) if row[1] in {"assembly", "tc-reviewer:canonical"}]
    assert [row[0] for row in stages] == ["CANDIDATE_PUBLISHED", "REVIEW_REQUESTED", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED"], stages
    assert _ledger(run)["status"] == "COMPLETED" and _boundary(run)["host_isolation"]["fresh_context"] is True


def test_prepare_without_the_fresh_context_statement_is_refused_before_it_writes(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _open(run, fresh=None)
    assert code == 2 and refused["code"] == "FRESH_CONTEXT_REQUIRED", refused
    assert "--fresh-context yes" in refused["fix"] and "accept-self-review" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added


def test_a_run_that_admitted_self_review_opens_with_fresh_context_no(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, self_review=True)
    code, opened = _open(run, fresh="no")
    assert code == 0, opened
    assert _boundary(run)["host_isolation"]["fresh_context"] is False
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected
    # the host that does say yes keeps its word
    other = _generated_run(tmp_path / "other", self_review=True)
    assert _open(other, fresh="yes")[0] == 0
    assert _boundary(other)["host_isolation"]["fresh_context"] is True
    assert _boundary(other)["host_isolation"]["evidence_digest"] != _boundary(run)["host_isolation"]["evidence_digest"]  # the statement is part of the evidence


# --------------------------------------------------------------------------------------
# submit
# --------------------------------------------------------------------------------------

def test_submit_with_no_answer_in_the_slot_says_so_and_writes_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    before = _journal(run["run_root"])
    code, refused = _cycle("submit", run)
    assert code == 2 and refused["code"] == "ANSWER_FILE_UNREADABLE", refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added


def _wrong_verdict(run: dict) -> str:
    _candidate_file(run)
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    wrong = json.loads(Path(_review_answer(run)).read_bytes())
    wrong["artifacts"]["validation_report"]["reviewed_case_ids"] = [candidate["test_cases"][0]["case_id"]]  # not every case
    return _put(run["tmp"] / "bad.json", wrong)


def test_a_bad_verdict_keeps_the_one_retry(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    bad = _wrong_verdict(run)
    before = _journal(run["run_root"])
    code, refused = _select_verdict(run, bad)
    assert code == 2 and "REVIEW_DECISION_INVALID" in refused["message"] and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before and _ledger(run)["status"] == "WAITING"
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    code, retried = _select_verdict(run, _review_answer(run), "--transport-attempts", "2")
    assert code == 0 and retried["result"]["status"] == "EFFECTIVE_SELECTED", retried
    response = next(row for row in _events(run["run_root"]) if row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row["stage_instance_id"] == "tc-reviewer:canonical")
    assert response["transport_attempts"] == 2


# --------------------------------------------------------------------------------------
# --dry-run
# --------------------------------------------------------------------------------------

def _autofix(run: dict) -> str:
    _candidate_file(run)
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    case = candidate["test_cases"][0]
    from tools.canonical_document import document_sha256
    from tools.review_edits import reviewer_successor

    value = json.loads(Path(_review_answer(run)).read_bytes())
    report = value["artifacts"]["validation_report"]
    report["verdict"] = "AUTO_FIX_APPLIED"
    report["corrections"] = [{"id": "FIX-1", "correction_kind": "MECHANICAL", "related_ids": [case["case_id"]], "description": "опечатка в названии", "evidence": ["REQ"]}]
    value["artifacts"]["successor_edits"] = [{"correction_id": "FIX-1", "case_id": case["case_id"], "field": "title", "old": case["title"], "new": case["title"] + "."}]
    successor = reviewer_successor(candidate, value["artifacts"])
    report["effective"] = {"document_id": successor["document_id"], "revision": successor["revision"], "document_sha256": document_sha256(successor)}
    return _put(run["tmp"] / "autofix.json", value)


@pytest.mark.parametrize("verdict", ["ПРИНЯТО", "ТРЕБУЕТ ДОРАБОТКИ", "AUTO_FIX_APPLIED"])
def test_dry_run_changes_nothing_byte_for_byte(tmp_path: Path, verdict: str) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    answer = _autofix(run) if verdict == "AUTO_FIX_APPLIED" else _verdict(run, verdict)
    _slot(run, answer)
    before = _tree(run["run_root"]), _tree(run["project"])
    code, dry = _select_verdict(run, answer, "--dry-run")
    assert code == 0 and dry["dry_run"] is True and dry["status"] == "ok", dry
    assert _tree(run["run_root"]) == before[0] and _tree(run["project"]) == before[1]  # the journal, the ledger, the bundles, tmp: all
    expected = {"ПРИНЯТО": "EFFECTIVE_SELECTED", "ТРЕБУЕТ ДОРАБОТКИ": "REWORK", "AUTO_FIX_APPLIED": "EFFECTIVE_SELECTED"}[verdict]
    assert dry["result"]["status"] == expected, dry
    would = dry["result"]["would_publish"]
    assert would["reviewer_ledger"]["status"] == "COMPLETED"
    if verdict == "AUTO_FIX_APPLIED":
        assert would["effective_source"]["revision"] == 2
    # the real call afterwards gives the very result the dry run announced
    code, real = _select_verdict(run, answer)
    assert code == 0 and real["result"]["status"] == expected, real
    assert real["result"]["effective_source"] == dry["result"]["effective_source"]


def test_dry_run_of_a_wrong_verdict_refuses_and_still_writes_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    bad = _wrong_verdict(run)
    _slot(run, bad)
    before = _tree(run["run_root"]), _tree(run["project"])
    code, refused = _select_verdict(run, bad, "--dry-run")
    assert code == 2 and "REVIEW_DECISION_INVALID" in refused["message"], refused
    assert _tree(run["run_root"]) == before[0] and _tree(run["project"]) == before[1]  # not even refusals.jsonl


def test_dry_run_after_the_selection_writes_nothing_either(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    answer = _verdict(run)
    code, real = _select_verdict(run, answer)
    assert code == 0, real
    _slot(run, answer)
    before = _tree(run["run_root"]), _tree(run["project"])
    code, dry = _select_verdict(run, answer, "--dry-run")
    assert code == 0 and dry["dry_run"] is True, dry
    assert _tree(run["run_root"]) == before[0] and _tree(run["project"]) == before[1]


# --------------------------------------------------------------------------------------
# the normalization of P1-fix2 on the path of prepare (hunt 6.1)
# --------------------------------------------------------------------------------------

def test_prepare_publishes_the_candidate_of_an_answer_with_a_mixed_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer of a batch that lists requirement ids and categories out of order is stored in order (model-response) and assembled
    in order (assemble_candidate); prepare, which assembles from the journal, publishes it and opens the review — the one
    assembly per process does not go round the normalization."""
    import copy

    from tests import test_batch_assembly

    real = test_batch_assembly._fragment

    def mixed(plan, context, index):
        fragment = real(plan, context, index)
        if index != 0:
            return fragment
        first = fragment["requirements"][0]
        second = dict(first, requirement_id=first["requirement_id"] + "-B", display_order=100)
        fragment["requirements"].append(second)
        fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
        fragment["test_cases"][0]["requirement_ids"] = [second["requirement_id"], first["requirement_id"]]
        fragment["test_cases"][0]["categories"] = ["functional", "positive"]
        fragment = copy.deepcopy(fragment)
        fragment["digest"] = ""  # empty: the code puts it
        return fragment

    monkeypatch.setattr(test_batch_assembly, "_fragment", mixed)
    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0 and opened["status"] == "ok", opened
    assert _shape(run["run_root"])[-1][0] == "MODEL_REQUESTED"
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate = journal_assembly(run["run_root"], run["attempt_id"])[0]
    case = candidate["test_cases"][0]
    assert case["categories"] == ["positive", "functional"] or case["categories"] != ["functional", "positive"], case["categories"]
    assert case["requirement_ids"] == sorted(case["requirement_ids"], key=[row["requirement_id"] for row in candidate["requirements"]].index)
    assert _select_verdict(run, _verdict(run))[0] == 0
