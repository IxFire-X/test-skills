"""FaT-U5 on the cycle of the agent: the inputs of the review come from the journal — ``prepare --stage tc-reviewer:canonical`` takes no file.

- a step that is not ready (the generator never answered) is refused with its code and fix, nothing written;
- every CLI answers a refusal in one envelope {status, code, message, fix, next}."""

from __future__ import annotations

from pathlib import Path

from tests.journal_helpers import assert_refused_once
from tests.test_review_requested import _generated_run, _journal
from tests.test_review_two_commands import _open


def test_a_missing_step_is_unavailable_with_its_fix(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)  # the generator never answered
    before = _journal(run["run_root"])
    code, refused = _open(run)
    assert code == 2 and refused.get("fix") and set(refused) >= {"status", "code", "message", "fix", "next"}, refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], "prepare")  # J1: the one event the refusal itself added


def test_every_cli_refuses_in_one_envelope(tmp_path: Path, capsys) -> None:
    import json

    from tools.run_pipeline import main

    run = _generated_run(tmp_path, generated=False)
    code, api = _open(run)
    assert code == 2 and set(api) >= {"status", "code", "message", "fix", "next"} and api["status"] == "error", api
    capsys.readouterr()
    assert main(["status", "--project", str(run["project"]), "--run", "0" * 32]) == 2
    pipeline = json.loads(capsys.readouterr().out)
    assert set(pipeline) >= {"status", "code", "message", "fix", "next"} and pipeline["code"] == pipeline["reason"], pipeline
