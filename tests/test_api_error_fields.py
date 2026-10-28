"""FaT-U6: tools.api and the orchestrate CLI print a refusal's own code and its fix — not `ValueError` for almost all of
pilot_state.  The message stays whole for its readers; a `; fix: …` tail is also given as its own field."""

from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize(("error", "fields"), [
    (ValueError("invalid factual receipt"), {"code": "ValueError", "message": "invalid factual receipt"}),
    (ValueError("GENERATOR_INPUT_DIGEST: the plan digest is not its self-digest"),
     {"code": "GENERATOR_INPUT_DIGEST", "message": "GENERATOR_INPUT_DIGEST: the plan digest is not its self-digest"}),
    (ValueError("GENERATOR_INPUT_ORDER: the header is not published yet; fix: first tools.api publish-generator-input"),
     {"code": "GENERATOR_INPUT_ORDER", "message": "GENERATOR_INPUT_ORDER: the header is not published yet; fix: first tools.api publish-generator-input",
      "fix": "first tools.api publish-generator-input"}),
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
    from tools import api, pilot_state

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(pilot_state, "read_attempt_receipt", refuse)
    code = api.main(["read-receipt", "--run-root", "x", "--attempt", "a" * 32, "--kind", "execution-receipt", "--event", "ARTIFACT_READ_BACK"])
    return code, json.loads(capsys.readouterr().out)


def test_api_prints_the_prefixed_code_of_a_pilot_state_refusal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, output = _api(monkeypatch, capsys, ValueError("GENERATOR_INPUT_DIGEST: the header digest x is not its self-digest y"))
    assert code == 2 and output["status"] == "error" and output["code"] == "GENERATOR_INPUT_DIGEST"
    assert output["message"] == "GENERATOR_INPUT_DIGEST: the header digest x is not its self-digest y" and "fix" not in output


def test_api_gives_the_fix_tail_as_its_own_field_too(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import ModelResponseDigestMismatch

    error = ModelResponseDigestMismatch("MODEL_RESPONSE_DIGEST_MISMATCH: tc-generator:BATCH-a: /plan_digest: дан x, ожидается y — "
                                        "журнал; fix: это ошибка ответа — исправь поля")
    _code, output = _api(monkeypatch, capsys, error)
    assert output["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH" and output["fix"] == "это ошибка ответа — исправь поля"
    assert output["message"] == str(error)  # old readers find the fix text where it was


def test_a_plain_error_keeps_its_type_as_before(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _code, output = _api(monkeypatch, capsys, ValueError("invalid factual receipt"))
    assert output["code"] == "ValueError" and output["message"] == "invalid factual receipt"


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


def test_a_missing_fragment_names_its_path(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    """Of three --fragment files the answer names the missing one."""
    from tools import api

    present = tmp_path / "fragment-1.json"
    present.write_text("{}", encoding="utf-8")
    missing = tmp_path / "fragment-2.json"
    with pytest.raises(SystemExit) as stop:
        api.main(["assemble", "--header", str(present), "--plan", str(present), "--fragment", str(present), "--fragment", str(missing)])
    output = json.loads(capsys.readouterr().out)
    assert stop.value.code == 2 and output["status"] == "error"
    assert json.dumps(str(missing), ensure_ascii=False)[1:-1] in json.dumps(output, ensure_ascii=False)


def test_an_os_error_without_a_file_has_no_empty_tail() -> None:
    from tools.json_cli import error_fields

    assert error_fields(OSError(5, "Input/output error"))["message"] == "OSError: Input/output error"
