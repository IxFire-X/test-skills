"""Audit U2: the digests the code knows are put by the code — model-request takes its inputs from the journal when no
--input-digest is given; model-response (and the check of a fragment) put the binding fields an answer leaves out and refuse
the ones given wrong, as before."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

BINDINGS = ("plan_digest", "header_digest", "context_receipt_digest", "digest")


def _stage(run: dict) -> str:
    return f"tc-generator:{run['plan']['batches'][0]['batch_id']}"


def test_without_input_digest_the_request_takes_the_inputs_the_journal_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import import _generator_request, _to_generator
    from tools import pilot_state

    run = _to_generator(tmp_path, monkeypatch)
    code, requested = _generator_request(run)
    assert code == 0, requested
    state = pilot_state.derive_state(run["root"])
    attempt = state["attempts"][-1]
    expected = [digest for digest, _source in pilot_state._expected_model_inputs(run["project"].resolve(), run["root"].resolve(), state, attempt, _stage(run))]
    event = next(row for row in state["events"] if row["event_type"] == "MODEL_REQUESTED" and row.get("stage_instance_id") == _stage(run))
    assert pilot_state.read_model_request(run["root"], run["attempt_id"], _stage(run), event["artifact_digest"])["input_digests"] == expected


def test_without_input_digest_an_unpublished_plan_is_the_refusal_of_before(tmp_path: Path) -> None:
    from tests.test_step_inputs import _generator_run
    from tools import pilot_state

    root, attempt_id, *_rest = _generator_run(tmp_path)  # plan and header as plain run files, not generator inputs
    with pytest.raises(pilot_state.ModelInputsMismatch, match="MODEL_INPUTS_MISMATCH.*fix"):
        pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="g", invocation_id="gen", input_digests=[])


def test_a_stage_whose_inputs_the_journal_does_not_fix_still_needs_input_digest(tmp_path: Path) -> None:
    from tests.test_marker_parts import _run
    from tools import pilot_state

    root, attempt_id, _baseline, _receipts = _run(tmp_path, files=1)
    with pytest.raises(pilot_state.ModelRequestInvalid, match="no --input-digest given; fix: --input-digest") as refused:
        pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker", input_digests=[])
    assert "MODEL_INPUTS_REQUIRED" in str(refused.value) and refused.value.code == "MODEL_REQUEST_INVALID"


def _fragment(run: dict) -> dict:
    from tests.test_cases_import import _manual_fragment

    return _manual_fragment(run["plan"], run["context"], run["imported"], 0)


def test_a_fragment_without_its_binding_fields_gets_them_from_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import import _generator_request, _to_generator
    from tools import pilot_state

    run = _to_generator(tmp_path, monkeypatch)
    assert _generator_request(run)[0] == 0
    good = _fragment(run)
    bare = {key: value for key, value in good.items() if key not in BINDINGS}
    published = pilot_state.publish_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), bare)
    assert sorted(published["filled"]) == sorted("/" + key for key in BINDINGS), published
    stored = pilot_state.read_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), published["content_digest"])["artifact"]
    assert stored == good  # the very fragment with the fields, byte for byte the same digest
    assert published["content_digest"] == pilot_state._model_stage_artifact_digest(good)


def test_a_binding_field_given_wrong_is_refused_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import import _generator_request, _to_generator
    from tools import pilot_state

    run = _to_generator(tmp_path, monkeypatch)
    assert _generator_request(run)[0] == 0
    wrong = {key: value for key, value in _fragment(run).items() if key != "digest"}
    wrong["header_digest"] = run["plan_digest"]
    with pytest.raises(ValueError, match="MODEL_RESPONSE_DIGEST_MISMATCH: .*/header_digest"):
        pilot_state.publish_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), wrong)


def test_a_full_answer_is_stored_as_before_and_names_nothing_filled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import import _generator_request, _to_generator
    from tools import pilot_state

    run = _to_generator(tmp_path, monkeypatch)
    assert _generator_request(run)[0] == 0
    good = _fragment(run)
    published = pilot_state.publish_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), good)
    assert "filled" not in published and published["content_digest"] == pilot_state._model_stage_artifact_digest(good)


def test_validate_fragment_accepts_what_model_response_will_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cases_import import _to_generator
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    run = _to_generator(tmp_path, monkeypatch)
    bare = {key: value for key, value in _fragment(run).items() if key not in BINDINGS}
    validate_fragment(bare, run["plan"], run["header"], run["context"], None, run["imported"])
    with pytest.raises(BatchAssemblyError, match="header digest"):
        validate_fragment(dict(bare, header_digest=run["plan_digest"]), run["plan"], run["header"], run["context"], None, run["imported"])
    assert json.dumps(bare)  # the caller's fragment is not changed in place
    assert "digest" not in bare


def test_a_refused_retry_does_not_advise_the_retry_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit 2 (FaT-AB): the answer given as the one retry and refused names the way out of the stage, not a third call."""
    from tests.test_cases_import import _generator_request, _to_generator
    from tools import pilot_state
    from tools.json_cli import error_fields

    run = _to_generator(tmp_path, monkeypatch)
    assert _generator_request(run)[0] == 0
    wrong = dict(_fragment(run), header_digest=run["plan_digest"])
    with pytest.raises(ValueError) as first:
        pilot_state.publish_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), wrong)
    assert "единственным повтором стадии (--transport-attempts 2)" in error_fields(first.value)["fix"]
    with pytest.raises(ValueError) as second:
        pilot_state.publish_model_stage_artifact(run["root"], run["attempt_id"], _stage(run), wrong, transport_attempts=2)
    fields = error_fields(second.value)
    assert fields["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH" and "/header_digest" in fields["message"], fields
    assert fields["fix"].startswith("повтор стадии исчерпан") and "finish" in fields["fix"], fields
    assert "единственным повтором" not in fields["message"] and "отдай" not in fields["fix"], fields
