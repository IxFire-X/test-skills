"""FaT-BO: the batches of one plan go one after another through the journal — batch 1 has four inputs, batch k>=2 a fifth
one, the capabilities of the accepted earlier batches, which code builds from the journal; a later batch that changes an
earlier capability is refused before the write and keeps its one retry. The chain runs through `python -m tools.api`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.helpers import answer_stage, prepare_stage, submit_stage
from tests.test_digest_traps import _publish_inputs
from tests.test_review_requested import _generated_run, _journal
from tests.journal_helpers import assert_refused_once

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path) -> tuple[dict, str, str, list[dict]]:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    return run, plan_digest, header_digest, fragments


def _prior(run: dict, batch_id: str) -> tuple[int, dict]:
    """The capabilities of the batches before ``batch_id`` — what ``prepare`` publishes before the request; the refusal as the CLI gives it."""
    from tools.json_cli import error_envelope
    from tools.pilot_state import publish_prior_capabilities

    try:
        return 0, {"result": publish_prior_capabilities(run["run_root"], run["attempt_id"], batch_id)}
    except ValueError as error:
        return 2, error_envelope(error)


def _to_the_second_batch(run: dict, fragments: list[dict]) -> str:
    """Batch 1 is prepared and answered, batch 2 prepared; the stage of batch 2."""
    first, second = (f"tc-generator:{fragment['batch_id']}" for fragment in fragments)
    assert answer_stage(run["run_root"], first, run["fragments"][0])[0] == 0
    code, prepared = prepare_stage(run["run_root"], second)
    assert code == 0, prepared
    return second


def test_batch_one_has_four_inputs_and_batch_two_a_fifth_built_from_the_journal(tmp_path: Path) -> None:
    from tools.pilot_state import read_model_request

    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    code, prepared = prepare_stage(run["run_root"], f"tc-generator:{first}")
    assert code == 0, prepared
    request = read_model_request(run["run_root"], run["attempt_id"], f"tc-generator:{first}", prepared["result"]["request_digest"])
    assert len(request["input_digests"]) == 4 and request["input_digests"][2:] == [plan_digest, header_digest], request
    assert submit_stage(run["run_root"], f"tc-generator:{first}", run["fragments"][0])[0] == 0
    code, refused = _prior(run, first)
    assert code == 2 and "первая пачка" in refused["message"], refused

    code, prior = _prior(run, second)
    assert code == 0 and prior["result"]["capabilities"] == 1, prior
    digest = prior["result"]["digest"]
    code, prepared = prepare_stage(run["run_root"], f"tc-generator:{second}")
    assert code == 0, prepared
    request = read_model_request(run["run_root"], run["attempt_id"], f"tc-generator:{second}", prepared["result"]["request_digest"])
    assert len(request["input_digests"]) == 5 and request["input_digests"][4] == digest, request

    before = _journal(run["run_root"])
    code, again = _prior(run, second)
    assert code == 0 and again["result"]["digest"] == digest and _journal(run["run_root"]) == before, again


def test_a_changed_capability_of_batch_two_is_refused_before_the_write_and_keeps_the_retry(tmp_path: Path) -> None:
    from tools.batch_assembly import with_digest

    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    second = _to_the_second_batch(run, fragments)

    changed = {key: item for key, item in fragments[1].items() if key != "digest"}
    changed["operation_capabilities"] = [dict(changed["operation_capabilities"][0], action="different")]
    before = _journal(run["run_root"])
    code, refused = submit_stage(run["run_root"], second, with_digest(changed))
    assert code == 2 and refused["code"] == "BATCH_CAPABILITY_CONFLICT" and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    code, answered = submit_stage(run["run_root"], second, run["fragments"][1], "--transport-attempts", "2")
    assert code == 0, answered
    from tools.orchestrate_test_case_revision import journal_assembly

    assert journal_assembly(run["run_root"], run["attempt_id"])[0]["test_cases"]  # both answers are the journal's: the review assembles them


def test_step_size_finds_the_fifth_input_of_batch_two(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    second = _to_the_second_batch(run, fragments)

    completed = subprocess.run([sys.executable, "-m", "tools.step_size", "--project", str(run["project"]), "--run", Path(run["run_root"]).name,
                                "--stage", second], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    report = json.loads(completed.stdout)
    assert completed.returncode == 0 and report.get("warning") != "STEP_INPUT_UNRESOLVED", report
    assert not report.get("unresolved"), report
