"""FaT-U6: tools.api and the orchestrate CLI print a refusal's own code and its fix — not `ValueError` for almost all of
pilot_state.  The message stays whole for its readers; a `; fix: …` tail is also given as its own field."""

from __future__ import annotations

import json

import pytest

from tools.fixes import fix_for

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(("error", "fields"), [
    # V8 (A5-10): a bare ValueError is INPUT_INVALID with the fix of the table; a code with no fix anywhere keeps no fix
    (ValueError("invalid factual receipt"), {"code": "INPUT_INVALID", "message": "invalid factual receipt", "fix": fix_for("INPUT_INVALID")}),
    (ValueError("UNLISTED_REFUSAL: the plan digest is not its self-digest"),
     {"code": "UNLISTED_REFUSAL", "message": "UNLISTED_REFUSAL: the plan digest is not its self-digest"}),
    (ValueError("STAGE_NOT_PREPARED: the stage has no request yet; fix: prepare --stage x"),
     {"code": "STAGE_NOT_PREPARED", "message": "STAGE_NOT_PREPARED: the stage has no request yet; fix: prepare --stage x", "fix": "prepare --stage x"}),
    (KeyError("missing"), {"code": "KeyError", "message": "'missing'"}),
])
def test_the_code_is_the_error_s_own_then_its_prefix_then_its_type(error: Exception, fields: dict) -> None:
    from tools.json_cli import error_fields

    assert error_fields(error) == fields


def test_an_error_s_own_code_and_fix_win() -> None:
    from tools.json_cli import error_fields

    class Refusal(ValueError):
        code = "OWN_CODE"
        fix = "own fix"

    assert error_fields(Refusal("OTHER: text; fix: tail")) == {"code": "OWN_CODE", "message": "OTHER: text; fix: tail", "fix": "own fix"}


def _api(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception) -> tuple[int, dict]:
    from tools import api, stage_steps

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(stage_steps, "prepare", refuse)
    code = api.main(["prepare", "--run-root", "x", "--stage", "tc-generator:b1"])
    return code, json.loads(capsys.readouterr().out)


def test_api_prints_the_prefixed_code_of_a_pilot_state_refusal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, output = _api(monkeypatch, capsys, ValueError("UNLISTED_REFUSAL: the header digest x is not its self-digest y"))
    assert code == 2 and output["status"] == "error" and output["code"] == "UNLISTED_REFUSAL"
    # FaT-U5: the one envelope always carries fix (null when the refusal names none)
    assert output["message"] == "UNLISTED_REFUSAL: the header digest x is not its self-digest y" and output["fix"] is None


def test_api_gives_the_fix_tail_as_its_own_field_too(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import ModelResponseDigestMismatch

    error = ModelResponseDigestMismatch("MODEL_RESPONSE_DIGEST_MISMATCH: tc-generator:BATCH-a: /plan_digest: дан x, ожидается y — "
                                        "журнал; fix: это ошибка ответа — исправь поля")
    _code, output = _api(monkeypatch, capsys, error)
    assert output["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH" and output["fix"] == "это ошибка ответа — исправь поля"
    assert output["message"] == str(error)  # old readers find the fix text where it was


def test_a_plain_error_keeps_its_type_as_before(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _code, output = _api(monkeypatch, capsys, ValueError("invalid factual receipt"))
    assert output["code"] == "INPUT_INVALID" and output["message"] == "invalid factual receipt" and output["fix"] == fix_for("INPUT_INVALID")


@pytest.mark.parametrize("error", [KeyError("attempt_id"), TypeError("'NoneType' object is not subscriptable"),
                                   PermissionError(13, "Permission denied", "run/tmp/fragment-2.json")])
def test_run_pipeline_answers_json_instead_of_a_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                          error: Exception) -> None:
    """FaT-F1: KeyError, TypeError and OSError of a run_pipeline command are a JSON refusal with exit 2, never a traceback."""
    from tools import run_pipeline

    def refuse(_args):
        raise error

    monkeypatch.setattr(run_pipeline, "cmd_status", refuse)
    code = run_pipeline.main(["status", "--project", ".", "--run", "a" * 32])
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["status"] == "error" and output["code"] == type(error).__name__ == output["reason"]
    if isinstance(error, OSError):  # the file is the agent's own input: named, so it knows which one
        assert output["message"] == "PermissionError: Permission denied: run/tmp/fragment-2.json"


def test_an_os_error_without_a_file_has_no_empty_tail() -> None:
    from tools.json_cli import error_fields

    assert error_fields(OSError(5, "Input/output error"))["message"] == "OSError: Input/output error"
