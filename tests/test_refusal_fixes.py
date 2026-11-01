"""FaT-AU1 (audit C7): frequent refusals name their condition, carry a code and a fix; the checks themselves are unchanged."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _generated_run


def _request(run: dict, **extra):
    from tools.pilot_state import publish_model_request

    arguments = dict(model_id="model-generator", invocation_id="invoke-x", input_digests=["sha256:" + "1" * 64])
    arguments.update(extra)
    return publish_model_request(run["run_root"], run["attempt_id"], extra.pop("stage", "tc-generator:BATCH-x"), **arguments)


@pytest.mark.parametrize(("extra", "named"), [
    ({"input_digests": []}, "no --input-digest"),
    ({"input_digests": ["sha256:" + "1" * 64, "sha256:" + "1" * 64]}, "an --input-digest is repeated"),
    ({"input_digests": ["sha256:short"]}, "an --input-digest is not sha256:<64 hex>"),
    ({"invocation_id": "bad id with spaces"}, "--invocation-id is not a safe label"),
])
def test_an_invalid_model_request_names_its_condition(tmp_path: Path, extra: dict, named: str) -> None:
    from tools.pilot_state import ModelRequestInvalid, publish_model_request

    run = _generated_run(tmp_path, generated=False)
    arguments = {"model_id": "model-generator", "invocation_id": "invoke-x", "input_digests": ["sha256:" + "1" * 64], **extra}
    with pytest.raises(ModelRequestInvalid) as refused:
        publish_model_request(run["run_root"], run["attempt_id"], "tc-generator:BATCH-x", **arguments)
    assert str(refused.value).startswith("invalid model request: ") and named in str(refused.value)
    assert refused.value.code == "MODEL_REQUEST_INVALID"


def test_a_stage_outside_the_registry_is_named(tmp_path: Path) -> None:
    from tools.pilot_state import ModelRequestInvalid, publish_model_request

    run = _generated_run(tmp_path, generated=False)
    with pytest.raises(ModelRequestInvalid, match="stage 'tc-unknown:x' is not <stage>:<instance>"):
        publish_model_request(run["run_root"], run["attempt_id"], "tc-unknown:x", model_id="m", invocation_id="i",
                              input_digests=["sha256:" + "1" * 64])


def test_a_repeated_request_says_the_answer_goes_to_model_response(tmp_path: Path) -> None:
    from tools.pilot_state import ModelRequestDuplicate, publish_model_request

    run = _generated_run(tmp_path, generated=True)  # the generator requests are published
    stage = next(event["stage_instance_id"] for event in __import__("tools.pilot_state", fromlist=["derive_state"]).derive_state(run["run_root"])["events"]
                 if event["event_type"] == "MODEL_REQUESTED" and str(event.get("stage_instance_id")).startswith("tc-generator:"))
    before = (run["run_root"] / "events.jsonl").read_bytes()
    with pytest.raises(ModelRequestDuplicate) as refused:
        publish_model_request(run["run_root"], run["attempt_id"], stage, model_id="m", invocation_id="again",
                              input_digests=["sha256:" + "1" * 64])
    assert "do not repeat model-request" in str(refused.value) and "--transport-attempts 2" in str(refused.value)
    assert (run["run_root"] / "events.jsonl").read_bytes() == before


def test_a_schema_refusal_of_an_answer_carries_diagnostics_and_the_retry_prompt(tmp_path: Path) -> None:
    from tests.test_marker_parts import _run
    from tools.pilot_state import ModelStageArtifactInvalid, publish_model_request, publish_model_stage_artifact

    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    before = (root / "events.jsonl").read_bytes()
    with pytest.raises(ModelStageArtifactInvalid) as refused:
        publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", {"schema_version": "5.0.0", "stage": "context-marker"})
    assert (root / "events.jsonl").read_bytes() == before
    text = str(refused.value)
    assert text.startswith("invalid model stage artifact: context-marker:baseline does not pass context-marker-output.schema.json")
    assert "retry_prompt:" in text and "Ответ не прошёл проверку схемы" in text and "--transport-attempts 2" in text


def test_an_answer_without_its_request_names_the_missing_request(tmp_path: Path) -> None:
    from tools.batch_assembly import with_digest
    from tools.pilot_state import publish_model_stage_artifact

    run = _generated_run(tmp_path, generated=False)
    fragment = json.loads(Path(run["fragments"][0]).read_bytes())
    with pytest.raises(ValueError) as refused:
        publish_model_stage_artifact(run["run_root"], run["attempt_id"], f"tc-generator:{fragment['batch_id']}", with_digest(
            {key: value for key, value in fragment.items() if key != "digest"}))
    assert str(refused.value).startswith("MODEL_STAGE_NOT_REQUESTED") and "model-request" in str(refused.value)


def test_a_bad_candidate_receipt_has_a_reason_code_and_a_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import orchestrate_test_case_revision as revision

    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"document_id": "TCDOC-x"}), encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    flags = ["--candidate", str(candidate), "--candidate-receipt", str(receipt), "--assembly-receipt", str(candidate),
             "--inventory-receipt", str(candidate), "--context-receipt", str(candidate), "--context-marker-output", str(candidate),
             "--generator-fragment", str(candidate), "--reviewer-session", str(candidate), "--run-root", str(tmp_path),
             "--attempt-id", "a" * 32, "--output-dir", str(tmp_path / "out")]
    assert revision.main(["select", *flags]) == 2
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["reason_code"] == "ORCHESTRATION_RECEIPT_SHAPE" and "missing" in printed["message"] and "publish" in printed["fix"], printed


@pytest.mark.parametrize(("stage", "extra", "fixed"), [
    ("tc-generator:BATCH-x", {"input_digests": []}, "--input-digest на каждый вход"),
    ("tc-generator:BATCH-x", {"input_digests": ["sha256:" + "1" * 64, "sha256:" + "1" * 64]}, "каждый вход один раз"),
    ("tc-generator:BATCH-x", {"input_digests": ["sha256:short"]}, "sha256:<64 hex>"),
    ("tc-generator:BATCH-x", {"invocation_id": "bad id with spaces"}, "--invocation-id — метка"),
    ("tc-generator:BATCH-x", {"model_id": "sk-live-0123456789abcdefABCDEF0123456789"}, "--model-id — метка"),
    ("tc-unknown:x", {}, "--stage <stage>:<instance>, например context-marker:baseline"),
])
def test_every_invalid_model_request_carries_a_fix_field(tmp_path: Path, stage: str, extra: dict, fixed: str) -> None:
    """FaT-H4b (hunt 4, 4.2): each branch of MODEL_REQUEST_INVALID ends in «; fix: …», which error_fields gives apart."""
    from tools.json_cli import error_fields
    from tools.pilot_state import ModelRequestInvalid, publish_model_request

    run = _generated_run(tmp_path, generated=False)
    arguments = {"model_id": "model-generator", "invocation_id": "invoke-x", "input_digests": ["sha256:" + "1" * 64], **extra}
    with pytest.raises(ModelRequestInvalid) as refused:
        publish_model_request(run["run_root"], run["attempt_id"], stage, **arguments)
    fields = error_fields(refused.value)
    assert fields["code"] == "MODEL_REQUEST_INVALID" and fixed in fields["fix"], fields
    assert "sk-live" not in fields["fix"], "a secret-like value is never echoed in the fix"


def test_a_closed_attempt_names_the_scan_of_a_new_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path, generated=False)
    root = Path(run["run_root"])
    real = pilot_state.derive_state
    monkeypatch.setattr(pilot_state, "derive_state", lambda path: {**real(path), "attempts": [
        {**attempt, "state": "TERMINAL"} for attempt in real(path)["attempts"]]})
    with pytest.raises(pilot_state.ModelRequestInvalid) as refused:
        pilot_state.publish_model_request(root, run["attempt_id"], "tc-generator:BATCH-x", model_id="m", invocation_id="i",
                                          input_digests=["sha256:" + "1" * 64])
    fix = str(refused.value).split("; fix: ", 1)[1]
    assert "--new-run" in fix and ("run_pipeline scan" in fix or "scan с теми же" in fix), fix
