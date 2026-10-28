"""FaT-BO: the batches of one plan go one after another through the journal — batch 1 has four inputs, batch k>=2 a fifth
one, the capabilities of the accepted earlier batches, which code builds from the journal; a later batch that changes an
earlier capability is refused before the write and keeps its one retry. The chain runs through `python -m tools.api`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_digest_traps import _generator_request, _publish_inputs
from tests.test_review_requested import _api, _generated_run, _journal, _put

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path) -> tuple[dict, str, str, list[dict]]:
    run = _generated_run(tmp_path, generated=False)
    plan_digest, header_digest = _publish_inputs(run)
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    return run, plan_digest, header_digest, fragments


def _response(run: dict, batch_id: str, artifact: str, *extra: str) -> tuple[int, dict]:
    return _api("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{batch_id}",
                "--artifact", artifact, *extra)


def _prior(run: dict, batch_id: str) -> tuple[int, dict]:
    return _api("prior-capabilities", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--batch-id", batch_id)


def _second_request(run: dict, batch_id: str, plan_digest: str, header_digest: str, *fifth: str) -> tuple[int, dict]:
    context = json.loads(Path(run["context"]).read_bytes())["digest"]
    return _api("model-request", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{batch_id}",
                "--model-id", "model-generator", "--invocation-id", "invoke-generator-2",
                "--input-digest", run["marker_digest"], "--input-digest", context, "--input-digest", plan_digest, "--input-digest", header_digest,
                *[flag for digest in fifth for flag in ("--input-digest", digest)])


def test_batch_one_has_four_inputs_and_batch_two_a_fifth_built_from_the_journal(tmp_path: Path) -> None:
    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    code, refused = _second_request(run, first, plan_digest, header_digest, "sha256:" + "a" * 64)
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and "лишние входы" in refused["message"], refused
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    assert _response(run, first, run["fragments"][0])[0] == 0
    code, refused = _prior(run, first)
    assert code == 2 and "первая пачка" in refused["message"], refused

    code, refused = _second_request(run, second, plan_digest, header_digest)
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and "tools.api prior-capabilities" in refused["message"], refused
    code, prior = _prior(run, second)
    assert code == 0 and prior["result"]["capabilities"] == 1, prior
    digest = prior["result"]["digest"]
    code, refused = _second_request(run, second, plan_digest, header_digest)
    assert code == 2 and "вход 5" in refused["message"] and digest in refused["message"], refused
    code, requested = _second_request(run, second, plan_digest, header_digest, digest)
    assert code == 0, requested

    before = _journal(run["run_root"])
    code, again = _prior(run, second)
    assert code == 0 and again["result"]["digest"] == digest and _journal(run["run_root"]) == before, again


def test_batch_two_before_the_answer_of_batch_one_is_refused_by_name(tmp_path: Path) -> None:
    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    before = _journal(run["run_root"])
    code, refused = _prior(run, second)
    assert code == 2 and "сначала" in refused["message"] and first in refused["message"], refused
    code, refused = _second_request(run, second, plan_digest, header_digest, "sha256:" + "b" * 64)
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and first in refused["message"], refused
    assert _journal(run["run_root"]) == before


def test_a_changed_capability_of_batch_two_is_refused_before_the_write_and_keeps_the_retry(tmp_path: Path) -> None:
    from tools.batch_assembly import with_digest

    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    assert _response(run, first, run["fragments"][0])[0] == 0
    code, prior = _prior(run, second)
    assert code == 0, prior
    assert _second_request(run, second, plan_digest, header_digest, prior["result"]["digest"])[0] == 0

    changed = {key: item for key, item in fragments[1].items() if key != "digest"}
    changed["operation_capabilities"] = [dict(changed["operation_capabilities"][0], action="different")]
    before = _journal(run["run_root"])
    code, refused = _response(run, second, _put(run["tmp"] / "changed.json", with_digest(changed)))
    assert code == 2 and refused["code"] == "BATCH_CAPABILITY_CONFLICT" and "--transport-attempts 2" in refused["message"], refused
    assert _journal(run["run_root"]) == before
    code, answered = _response(run, second, run["fragments"][1], "--transport-attempts", "2")
    assert code == 0, answered
    code, assembled = _api("assemble", "--header", run["header"], "--plan", run["plan"], *[flag for path in run["fragments"] for flag in ("--fragment", path)],
                           "--out-candidate", str(run["tmp"] / "c.json"), "--out-receipt", str(run["tmp"] / "r.json"))
    assert code == 0, assembled


def test_step_size_finds_the_fifth_input_of_batch_two(tmp_path: Path) -> None:
    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first, second = fragments[0]["batch_id"], fragments[1]["batch_id"]
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    assert _response(run, first, run["fragments"][0])[0] == 0
    code, prior = _prior(run, second)
    assert code == 0, prior
    assert _second_request(run, second, plan_digest, header_digest, prior["result"]["digest"])[0] == 0

    completed = subprocess.run([sys.executable, "-m", "tools.step_size", "--project", str(run["project"]), "--run", Path(run["run_root"]).name,
                                "--stage", f"tc-generator:{second}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    report = json.loads(completed.stdout)
    assert completed.returncode == 0 and report.get("warning") != "STEP_INPUT_UNRESOLVED", report
    assert not report.get("unresolved"), report
