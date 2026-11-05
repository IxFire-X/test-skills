"""F2 (A2-4, arch review 3): prior-capabilities cut between its receipt file and the pair of events — the same command again finishes it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_digest_traps import _publish_inputs
from tests.test_review_open_recovery import _main
from tests.test_review_requested import _generated_run, _journal, _put
from tests.journal_helpers import assert_refused_once
from tests.test_cut_review import _crash_event, _crash_nth, _events



def _run_flags(run: dict) -> list[str]:
    return ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]


def _generator_inputs(run: dict, plan_digest: str, header_digest: str) -> list[str]:
    context = json.loads(Path(run["context"]).read_bytes())["digest"]
    return [run["marker_digest"], context, plan_digest, header_digest]


# --------------------------------------------------------------------------------------
# prior-capabilities: receipt file, ARTIFACT_PUBLISHED, ARTIFACT_READ_BACK
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"])
def test_prior_capabilities_cut_between_the_receipt_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    first_stage = f"tc-generator:{fragments[0]['batch_id']}"
    pilot_state.publish_model_request(run["run_root"], run["attempt_id"], first_stage, model_id="model-generator", invocation_id="invoke-1",
                                      input_digests=_generator_inputs(run, plan_digest, header_digest))
    pilot_state.publish_model_stage_artifact(run["run_root"], run["attempt_id"], first_stage, fragments[0])
    argv = ["prior-capabilities", *_run_flags(run), "--batch-id", fragments[1]["batch_id"]]
    with monkeypatch.context() as broken:
        _crash_event(broken, point, None)
        code, first = _main(capsys, argv)
        assert code == 2, first
    assert (run["run_root"] / "prior-capabilities" / f"{run['attempt_id']}.{fragments[1]['batch_id']}.json").exists()  # the cut left the file
    code, again = _main(capsys, argv)
    assert code == 0 and again["result"]["batch_id"] == fragments[1]["batch_id"], f"{point}: {again}"
    code, bound = _main(capsys, ["read-receipt", *_run_flags(run), "--kind", f"prior-capabilities-{fragments[1]['batch_id']}", "--event", "ARTIFACT_READ_BACK"])
    assert code == 0, bound  # the receipt is bound now: both events
    pair = [row["event_type"] for row in _events(run["run_root"]) if row.get("artifact_digest") == bound["result"]["digest"]]
    assert pair == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"], pair


def test_a_prior_capabilities_file_with_other_bytes_and_no_events_is_refused_with_its_code(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    first_stage = f"tc-generator:{fragments[0]['batch_id']}"
    pilot_state.publish_model_request(run["run_root"], run["attempt_id"], first_stage, model_id="model-generator", invocation_id="invoke-1",
                                      input_digests=_generator_inputs(run, plan_digest, header_digest))
    pilot_state.publish_model_stage_artifact(run["run_root"], run["attempt_id"], first_stage, fragments[0])
    argv = ["prior-capabilities", *_run_flags(run), "--batch-id", fragments[1]["batch_id"]]
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED", None)
        assert _main(capsys, argv)[0] == 2
    path = run["run_root"] / "prior-capabilities" / f"{run['attempt_id']}.{fragments[1]['batch_id']}.json"
    value = json.loads(path.read_bytes())
    value["schema_version"] = "9.9.9"
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    before = _journal(run["run_root"])
    code, refused = _main(capsys, argv)
    assert code == 2 and refused["code"] == "RECEIPT_FILE_UNBOUND" and refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added


# --------------------------------------------------------------------------------------
# J1 (A2-5): the control word of a generator request after a cut
# --------------------------------------------------------------------------------------

def _request_argv(run: dict, stage: str, inputs: list[str], invocation: str = "invoke-generator") -> list[str]:
    return ["model-request", *_run_flags(run), "--stage", stage, "--model-id", "model-generator", "--invocation-id", invocation,
            *[flag for digest in inputs for flag in ("--input-digest", digest)]]


def _crash_raw_event(monkeypatch, event_type: str) -> None:
    from tools import pilot_state

    _crash_nth(monkeypatch, pilot_state, "_append_event", lambda project, root, et, *a, **k: et == event_type)


def _generator_request(tmp_path: Path):
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    batch = json.loads(Path(run["fragments"][0]).read_bytes())["batch_id"]
    return run, f"tc-generator:{batch}", _generator_inputs(run, plan_digest, header_digest)


def test_a_request_cut_before_its_event_leaves_a_receipt_only_the_disk_knows(tmp_path: Path, monkeypatch, capsys) -> None:
    """The first call wrote the receipt (word N1) and died before MODEL_REQUESTED; the repeat writes its own (N2) and binds it.  The first
    receipt is an orphan on the disk: every reader goes by the events, so the run sees the word the agent was shown and no other."""
    from tools import review_canaries
    from tools.pilot_state import read_model_request
    from tools.run_report import build_report

    run, stage, inputs = _generator_request(tmp_path)
    with monkeypatch.context() as broken:
        _crash_raw_event(broken, "MODEL_REQUESTED")
        code, first = _main(capsys, _request_argv(run, stage, inputs))
        assert code == 2, first
    requests = run["run_root"] / "model-requests" / run["attempt_id"]
    cut_left = len(list(requests.glob("*.json")))  # the context-marker request of the run, and the generator's receipt the cut left
    code, again = _main(capsys, _request_argv(run, stage, inputs))
    assert code == 0, again
    bound = again["result"]["digest"]
    assert len(list(requests.glob("*.json"))) == cut_left + 1  # the orphan stays where it is, nothing deletes in a run
    shown = review_canaries.nonce_digest(again["result"]["nonce"])
    assert review_canaries.generator_nonce_digests(run["run_root"], run["attempt_id"]) == [shown]
    assert read_model_request(run["run_root"], run["attempt_id"], stage, bound)["nonce_sha256"] == shown
    report = build_report(run["project"], run["run_root"].name)[0]
    assert "CODEWORD_LOST" not in report["warnings"], report["warnings"]  # the word was shown once for the one request the run has


def test_a_repeated_request_after_its_event_says_the_word_is_lost_and_the_report_says_so(tmp_path: Path, capsys) -> None:
    from tools.run_report import build_report, render_markdown

    run, stage, inputs = _generator_request(tmp_path)
    code, first = _main(capsys, _request_argv(run, stage, inputs))
    assert code == 0 and first["result"]["nonce"], first
    receipts = len(list((run["run_root"] / "model-requests" / run["attempt_id"]).glob("*.json")))
    code, again = _main(capsys, _request_argv(run, stage, inputs))  # the agent lost the output and asks again
    assert code == 2 and again["code"] == "MODEL_REQUEST_DUPLICATE", again
    assert "CODEWORD_LOST" in again["message"] and "model-response" in again["fix"], again
    requests = run["run_root"] / "model-requests" / run["attempt_id"]
    assert len(list(requests.glob("*.json"))) == receipts + 0  # no second receipt
    report, _warnings = build_report(run["project"], run["run_root"].name)
    assert "CODEWORD_LOST" in report["warnings"] and report["refusals"]["codeword_lost"] == [stage], report["refusals"]
    assert "Контрольное слово генератора" in render_markdown(report)


def test_only_the_generator_request_has_a_word_to_lose(tmp_path: Path, capsys) -> None:
    from tools import pilot_state

    run, stage, inputs = _generator_request(tmp_path)
    assert _main(capsys, _request_argv(run, stage, inputs))[0] == 0
    state = pilot_state.derive_state(run["run_root"])
    assert pilot_state._codeword_lost_note(run["run_root"], state, run["attempt_id"], stage).strip().startswith("CODEWORD_LOST")
    assert pilot_state._codeword_lost_note(run["run_root"], state, run["attempt_id"], "context-marker:baseline") == ""
