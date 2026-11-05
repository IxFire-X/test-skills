"""F2 (A2-4, arch review 3): prior-capabilities cut between its receipt file and the pair of events — the same command again finishes it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_digest_traps import _publish_inputs
from tests.test_review_open_recovery import _main
from tests.test_review_requested import _generated_run, _journal, _put
from tests.test_cut_review import _crash_event, _crash_nth, _events


def _run_flags(run: dict) -> list[str]:
    return ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]


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
