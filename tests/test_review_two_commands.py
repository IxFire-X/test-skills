"""P2: the review in two commands — ``review-open`` then ``review-select --verdict``.

- the two commands leave the events of the nine (types, order, stages), the same boundary, ledger and effective canonical;
- ``review-open`` publishes an unpublished candidate itself, and again changes nothing; it takes ``fresh_context`` from a run
  that admitted SELF and asks for it otherwise;
- ``review-select --verdict`` records the answer, completes the session and selects; without ``--reviewer-session`` it takes
  the last revision of the ledger; the stage keeps its one retry (``--transport-attempts 2``);
- ``review-select --dry-run`` runs every check of the verdict and writes nothing, byte for byte, refusals included;
- the old commands keep working and say ``deprecated``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _complete, _generated_run, _journal, _put, _review_answer, _reviewed, _select


def _flags(run: dict) -> list[str]:
    return ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]


def _bundles(run: dict) -> str:
    return str(run["project"] / "bundles")


def _open(run: dict, *extra: str, fresh: str | None = "yes", invocation: str = "reviewer-subagent-1") -> tuple[int, dict]:
    fresh_flags = ["--fresh-context", fresh] if fresh is not None else []
    return _api("review-open", *_flags(run), "--output-dir", _bundles(run), "--model-id", "model-reviewer",
                "--invocation-id", invocation, *fresh_flags, *extra)


def _candidate_file(run: dict) -> None:
    """The file ``_review_answer`` reads the candidate from (the two-command path writes none itself)."""
    from tools.orchestrate_test_case_revision import journal_assembly

    _put(run["tmp"] / "candidate.json", journal_assembly(run["run_root"], run["attempt_id"])[0])


def _verdict(run: dict, verdict: str = "ПРИНЯТО") -> str:
    _candidate_file(run)
    return _review_answer(run, verdict)


def _select_verdict(run: dict, verdict: str, *extra: str) -> tuple[int, dict]:
    return _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--verdict", verdict, *extra)


def _events(run_root: Path) -> list[dict]:
    return [json.loads(line) for line in _journal(run_root).decode("utf-8").splitlines()]


def _shape(run_root: Path, without: str | None = None) -> list[tuple]:
    """Event type, stage and batch of every event; the batch ids of two runs differ, so they are numbered in order of appearance.
    ``without`` — the digest of a receipt whose event pair is left out (P7: ``review-open`` binds the canary receipt, the nine do not)."""
    batches: dict[str, int] = {}

    def name(value):
        if value is None or value == "reviewer-ledger-v1":
            return value
        return f"batch-{batches.setdefault(str(value).removeprefix('tc-generator:'), len(batches) + 1)}"

    return [(row["event_type"], name(row.get("stage_instance_id")) if str(row.get("stage_instance_id")).startswith("tc-generator:") else row.get("stage_instance_id"),
             name(row.get("batch_id"))) for row in _events(run_root) if without is None or row.get("artifact_digest") != without]


def _tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _ledger(run: dict) -> dict:
    code, read = _api("read-ledger", *_flags(run))
    assert code == 0, read
    return read["result"]


def _boundary(run: dict) -> dict:
    code, read = _api("read-receipt", *_flags(run), "--kind", "reviewer-session-boundary", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, read
    return read["result"]["record"]


# --------------------------------------------------------------------------------------
# the golden comparison: two commands = nine commands
# --------------------------------------------------------------------------------------

def test_two_commands_leave_the_events_of_the_nine(tmp_path: Path) -> None:
    old, review = _reviewed(tmp_path / "old", start="command")  # session-start, review-open, review-requested, model-request, model-response
    assert _complete(old)[0] == 0
    code, old_selected = _select(old, review)
    assert code == 0, old_selected

    new = _generated_run(tmp_path / "new")
    code, opened = _open(new)
    assert code == 0, opened
    code, selected = _select_verdict(new, _verdict(new))
    assert code == 0, selected

    code, canaries = _api("read-receipt", *_flags(new), "--kind", "review-canaries", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, canaries  # P7: review-open seeds the reviewer's view — one receipt, its event pair the only difference
    assert _shape(new["run_root"], without=canaries["result"]["digest"]) == _shape(old["run_root"])
    stages = [row for row in _shape(new["run_root"]) if row[1] in {"assembly", "tc-reviewer:canonical"}]
    assert [row[0] for row in stages] == ["CANDIDATE_PUBLISHED", "REVIEW_REQUESTED", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED"], stages
    # the boundary, the ledger (its events and isolation) and the effective canonical are the ones of the nine
    keys = ("generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes")
    assert {key: _boundary(new)[key] for key in keys} == {key: _boundary(old)[key] for key in keys}
    assert [event["event_type"] for event in _ledger(new)["events"]] == [event["event_type"] for event in _ledger(old)["events"]]
    assert _ledger(new)["status"] == _ledger(old)["status"] == "COMPLETED"
    assert _ledger(new)["host_isolation"] == _ledger(old)["host_isolation"]
    effective = [_api("read-effective", *_flags(run))[1]["result"] for run in (new, old)]
    assert effective[0]["document_digest"] == effective[1]["document_digest"]
    assert selected["result"]["status"] == old_selected["status"] == "EFFECTIVE_SELECTED"
    assert selected["result"]["effective_source"] == old_selected["effective_source"]


def test_the_package_and_the_boundary_are_those_the_old_session_start_gives(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import review_inputs, review_package

    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0, opened
    package = review_package(run["run_root"], run["attempt_id"], review_inputs(run["run_root"], run["attempt_id"], output_dir=Path(_bundles(run))))
    assert opened["result"]["package_binding"] == package
    boundary = opened["result"]["reviewer_session_boundary"]
    assert boundary["package_digest"] == package["package_digest"] and boundary["canonical_branch_digest"] == package["candidate_digest"]
    code, start = _api("session-start", *_flags(run), "--output-dir", _bundles(run), "--fresh-context", "yes",
                       "--reviewer-invocation-id", "reviewer-subagent-1", "--out", str(run["tmp"] / "start.json"))
    assert code == 0, start
    value = json.loads(Path(start["out"]).read_bytes())
    assert {key: boundary[key] for key in value} == value  # the start the old path would have built, field for field


def test_review_open_names_the_request_and_the_next_command(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0 and opened["status"] == "ok", opened
    result = opened["result"]
    assert result["review_requested"]["event_type"] == "REVIEW_REQUESTED" and result["review_requested"]["stage_instance_id"] == "tc-reviewer:canonical"
    request = result["model_request"]
    assert request["stage"] == "tc-reviewer" and request["invocation_id"] == "reviewer-subagent-1" and request["model_id"] == "model-reviewer"
    assert request["input_digests"] == [result["reviewer_session_boundary"]["canonical_branch_digest"], result["reviewer_session_boundary"]["package_digest"]]
    assert "review-select" in result["next_step"] and "--verdict" in result["next_step"] and run["attempt_id"] in result["next_step"]
    assert "deprecated" not in opened


# --------------------------------------------------------------------------------------
# review-open: the unpublished candidate, idempotence, the fresh context
# --------------------------------------------------------------------------------------

def test_review_open_publishes_the_candidate_itself_and_not_twice(tmp_path: Path) -> None:
    run = _generated_run(tmp_path / "a")
    assert not any(row[1] == "assembly" for row in _shape(run["run_root"]))
    assert _open(run)[0] == 0
    # the same events as: review-publish first, then review-open
    other = _generated_run(tmp_path / "b")
    assert _api("review-publish", *_flags(other), "--output-dir", _bundles(other))[0] == 0
    assert _open(other)[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"])
    assert sum(row[0] == "CANDIDATE_PUBLISHED" and row[1] == "assembly" for row in _shape(run["run_root"])) == 1


def test_a_second_review_open_changes_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    code, first = _open(run)
    assert code == 0, first
    before, files = _journal(run["run_root"]), _tree(run["run_root"] / "reviewer-session-ledgers")
    code, again = _open(run)
    assert code == 0, again
    assert _journal(run["run_root"]) == before and _tree(run["run_root"] / "reviewer-session-ledgers") == files
    assert again["result"]["reviewer_session_boundary"] == first["result"]["reviewer_session_boundary"]
    assert again["result"]["model_request"]["digest"] == first["result"]["model_request"]["digest"]


def test_a_second_review_open_with_another_reviewer_is_refused(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    before = _journal(run["run_root"])
    code, refused = _open(run, invocation="another-reviewer")
    assert code == 2 and refused["code"] == "REVIEW_ALREADY_OPEN" and refused["fix"], refused
    assert _journal(run["run_root"]) == before


def test_review_open_without_the_fresh_context_statement_is_refused_before_it_writes(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _open(run, fresh=None)
    assert code == 2 and refused["code"] == "FRESH_CONTEXT_REQUIRED", refused
    assert "--fresh-context yes" in refused["fix"] and "accept-self-review" in refused["fix"], refused
    assert _journal(run["run_root"]) == before


def test_a_run_that_admitted_self_review_takes_fresh_context_no_from_the_code(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, self_review=True)
    code, opened = _open(run, fresh=None)
    assert code == 0, opened
    assert opened["result"]["reviewer_session_boundary"]["host_isolation"]["fresh_context"] is False
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected
    # the host that does say yes keeps its word
    other = _generated_run(tmp_path / "other", self_review=True)
    assert _open(other, fresh="yes")[0] == 0
    assert _boundary(other)["host_isolation"]["fresh_context"] is True


def test_fresh_context_no_in_an_ordinary_run_is_independence_unverified(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _open(run, fresh="no")
    assert code == 2 and refused["code"] == "INDEPENDENCE_UNVERIFIED" and "accept-self-review" in refused["fix"], refused
    assert _journal(run["run_root"]) == before


def test_review_open_needs_the_model_and_the_invocation(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _api("review-open", *_flags(run), "--output-dir", _bundles(run), "--fresh-context", "yes")
    assert code == 2 and refused["code"] == "INVALID_ARGUMENT" and "--model-id" in refused["fix"] and "--invocation-id" in refused["fix"], refused
    assert _journal(run["run_root"]) == before


# --------------------------------------------------------------------------------------
# review-select --verdict
# --------------------------------------------------------------------------------------

def test_select_with_a_verdict_records_the_answer_and_selects(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED" and selected["result"]["effective_bundle_receipt"], selected
    assert sum(row[0] == "MODEL_RESPONSE_RECEIVED" and row[1] == "tc-reviewer:canonical" for row in _shape(run["run_root"])) == 1
    assert _ledger(run)["status"] == "COMPLETED"
    # the same command again answers the same selection and writes nothing
    before = _journal(run["run_root"])
    code, again = _select_verdict(run, str(run["tmp"] / "review.json"))
    assert code == 0 and again["result"] == selected["result"] and _journal(run["run_root"]) == before, again


def test_select_without_a_session_takes_the_last_revision_of_the_ledger(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    review = _verdict(run)
    code, answered = _api("model-response", *_flags(run), "--stage", "tc-reviewer:canonical", "--artifact", review)
    assert code == 0, answered
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run))  # no --verdict, no --reviewer-session
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected
    code, again = _api("review-select", *_flags(run), "--output-dir", _bundles(run))  # the ledger is now the completed one
    assert code == 0 and again["result"] == selected["result"], again


def test_select_of_an_aborted_session_is_the_last_revision_too(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, aborted = _api("review-abort", *_flags(run), "--reason", "REVIEW_UNUSABLE", "--out", str(run["tmp"] / "session.json"))
    assert code == 0, aborted
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", str(run["tmp"] / "session.json"))
    assert code == 0 and selected["result"]["facts"]["reason_code"] == "REVIEW_UNUSABLE", selected
    code, again = _api("review-select", *_flags(run), "--output-dir", _bundles(run))  # the aborted ledger is the last revision
    assert code == 0 and again["result"] == selected["result"], again


def test_select_with_no_answer_anywhere_says_how_to_give_it(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    before = _journal(run["run_root"])
    code, refused = _api("review-select", *_flags(run), "--output-dir", _bundles(run))
    assert code == 2 and refused["code"] == "REVIEWER_RESPONSE_MISSING" and "--verdict" in refused["fix"], refused
    assert _journal(run["run_root"]) == before


def test_select_after_the_attempt_is_closed_is_the_attempt_terminal_envelope(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import api, pilot_state

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    real = pilot_state.derive_state

    def closed(run_root):
        state = real(run_root)
        for attempt in state["attempts"]:
            attempt["state"] = "TERMINAL"
        return state

    monkeypatch.setattr(pilot_state, "derive_state", closed)
    code = api.main(["review-select", *_flags(run), "--output-dir", _bundles(run)])
    envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 2 and envelope["code"] == "ATTEMPT_TERMINAL" and "report.md" in envelope["fix"], envelope


def test_a_bad_verdict_keeps_the_one_retry(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    _candidate_file(run)
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    wrong = json.loads(Path(_review_answer(run)).read_bytes())
    wrong["artifacts"]["validation_report"]["reviewed_case_ids"] = [candidate["test_cases"][0]["case_id"]]  # not every case
    bad = _put(run["tmp"] / "bad.json", wrong)
    before = _journal(run["run_root"])
    code, refused = _select_verdict(run, bad)
    assert code == 2 and "REVIEW_DECISION_INVALID" in refused["message"] and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before and _ledger(run)["status"] == "WAITING"
    code, retried = _select_verdict(run, _review_answer(run), "--transport-attempts", "2")
    assert code == 0 and retried["result"]["status"] == "EFFECTIVE_SELECTED", retried
    response = next(row for row in _events(run["run_root"]) if row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row["stage_instance_id"] == "tc-reviewer:canonical")
    assert response["transport_attempts"] == 2


def test_another_verdict_after_the_answer_is_recorded_is_refused(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _select_verdict(run, _verdict(run))[0] == 0
    before = _journal(run["run_root"])
    code, refused = _select_verdict(run, _verdict(run, "ТРЕБУЕТ ДОРАБОТКИ"))
    assert code == 2 and refused["code"] == "MODEL_STAGE_ANSWERED", refused
    assert _journal(run["run_root"]) == before


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
    before = _tree(run["run_root"]), _tree(run["project"])
    code, dry = _select_verdict(run, answer, "--dry-run")
    assert code == 0 and dry["dry_run"] is True and dry["status"] == "ok", dry
    assert _tree(run["run_root"]) == before[0] and _tree(run["project"]) == before[1]  # the journal, the ledger, the bundles, tmp: all
    expected = {"ПРИНЯТО": "EFFECTIVE_SELECTED", "ТРЕБУЕТ ДОРАБОТКИ": "REWORK", "AUTO_FIX_APPLIED": "EFFECTIVE_SELECTED"}[verdict]
    assert dry["result"]["status"] == expected, dry
    would = dry["result"]["would_publish"]
    assert would["model_response"] and would["reviewer_ledger"]["status"] == "COMPLETED"
    if verdict == "AUTO_FIX_APPLIED":
        assert would["successor_revision"] == 2
    # the real call afterwards gives the very result the dry run announced
    code, real = _select_verdict(run, answer)
    assert code == 0 and real["result"]["status"] == expected, real
    assert real["result"]["effective_source"] == dry["result"]["effective_source"]


def test_dry_run_of_a_wrong_verdict_refuses_and_still_writes_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    _candidate_file(run)
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    wrong = json.loads(Path(_review_answer(run)).read_bytes())
    wrong["artifacts"]["validation_report"]["reviewed_case_ids"] = [candidate["test_cases"][0]["case_id"]]
    bad = _put(run["tmp"] / "bad.json", wrong)
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
    before = _tree(run["run_root"]), _tree(run["project"])
    code, dry = _select_verdict(run, answer, "--dry-run")
    assert code == 0 and dry["dry_run"] is True, dry
    assert _tree(run["run_root"]) == before[0] and _tree(run["project"]) == before[1]


# --------------------------------------------------------------------------------------
# the old commands: still there, saying deprecated
# --------------------------------------------------------------------------------------

def test_the_old_review_commands_work_and_say_deprecated(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    from tests.test_review_requested import _open_session, _review, _reviewer_request

    candidate, boundary = _open_session(run, "command")
    assert _api("session-start", *_flags(run), "--output-dir", _bundles(run), "--fresh-context", "yes",
                "--reviewer-invocation-id", "reviewer-subagent-1")[1]["deprecated"].startswith("tools.api review-open")
    code, requested = _review(run, candidate)
    assert code == 0 and requested["deprecated"].startswith("tools.api review-open"), requested
    code, request = _reviewer_request(run, boundary)
    assert code == 0 and request["deprecated"].startswith("tools.api review-open"), request
    code, response = _api("model-response", *_flags(run), "--stage", "tc-reviewer:canonical", "--artifact", _verdict(run))
    assert code == 0 and response["deprecated"].startswith("tools.api review-select"), response
    code, completed = _complete(run)
    assert code == 0 and completed["deprecated"].startswith("tools.api review-select"), completed
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", str(run["tmp"] / "session.json"))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED" and selected["deprecated"].startswith("tools.api review-select --verdict"), selected


def test_the_commands_of_other_stages_are_not_deprecated(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    code, digest = _api("review-requested", *_flags(run), "--stage", "autotest-reviewer:r1", "--digest", "sha256:" + "7" * 64)
    assert "deprecated" not in digest  # a refusal of the automation review, which has no two-command form
    code, request = _api("model-request", *_flags(run), "--stage", "tc-generator:BATCH-none", "--model-id", "m", "--invocation-id", "i")
    assert "deprecated" not in request


# --------------------------------------------------------------------------------------
# the normalization of P1-fix2 on the path of review-open (hunt 6.1)
# --------------------------------------------------------------------------------------

def test_review_open_publishes_the_candidate_of_an_answer_with_a_mixed_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer of a batch that lists requirement ids and categories out of order is stored in order (model-response) and assembled
    in order (assemble_candidate); review-open, which assembles from the journal, publishes it and opens the review — the one
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
    assert opened["result"]["review_requested"]["event_type"] == "REVIEW_REQUESTED"
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate = journal_assembly(run["run_root"], run["attempt_id"])[0]
    case = candidate["test_cases"][0]
    assert case["categories"] == ["positive", "functional"] or case["categories"] != ["functional", "positive"], case["categories"]
    assert case["requirement_ids"] == sorted(case["requirement_ids"], key=[row["requirement_id"] for row in candidate["requirements"]].index)
    assert _select_verdict(run, _verdict(run))[0] == 0
