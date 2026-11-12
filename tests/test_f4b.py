"""F4b: the envelope of every CLI, no deepcopy of scalars; the delivery of cases has no mode-B command.

- A3-2: ``review-requested`` is not a command of the cases delivery (the review of the cases goes by ``prepare`` / ``submit``);
- A5-5: ``MODEL_OUTPUT_TRUNCATED`` of ``--artifact`` and of the answer in the slot of the review is a refusal with its code and fix,
  recorded in ``refusals.jsonl`` — not ``INVALID_ARGUMENT`` with «check the flags»;
- A5-9: ``step_size``, ``review_view``, ``review_edits`` refuse with ``code`` and ``fix``;
- O-9: ``_plain`` leaves a scalar as it is."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile
from tests.test_review_requested import _api, _generated_run
from tests.test_review_two_commands import _open, _select_verdict

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("f4b") / "pack", "cases-only")


def _run(pack: Path, module: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8")


def _last_json(done: subprocess.CompletedProcess) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1])


# ---- A3-2 -------------------------------------------------------------------------------------------------------------

def test_review_requested_is_not_a_command_of_the_cases_only_delivery(cases_only_pack: Path) -> None:
    listing = _run(cases_only_pack, "tools.api", "--help").stdout
    assert "review-requested" not in listing
    refused = _run(cases_only_pack, "tools.api", "review-requested", "--help")
    assert refused.returncode == 2 and "invalid choice" in refused.stdout + refused.stderr
    assert _run(cases_only_pack, "tools.api", "prepare", "--help").returncode == 0
    assert _run(cases_only_pack, "tools.api", "submit", "--help").returncode == 0


# ---- A5-5 -------------------------------------------------------------------------------------------------------------

def _truncated(tmp_path: Path) -> str:
    path = tmp_path / "cut.json"
    path.write_text('{"schema_version": "5.0.0", "artifacts": {"requirements": [{"id": "SREQ-1", "text": "обор', encoding="utf-8")
    return str(path)


def test_a_truncated_answer_is_a_refusal_with_its_code_its_fix_and_a_line_in_the_refusals(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    code, printed = _api("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "context-marker:baseline",
                         "--artifact", _truncated(run["tmp"]))  # SEC-A: the answer lies in the run
    assert code == 2 and printed["code"] == "MODEL_OUTPUT_TRUNCATED", printed
    assert printed["fix"] and "флаги" not in printed["fix"] and "--help" not in printed["fix"]
    lines = [json.loads(row) for row in (run["run_root"] / "refusals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["code"] == "MODEL_OUTPUT_TRUNCATED" and row["command"] == "model-response" for row in lines), lines


def test_a_truncated_answer_of_the_review_is_refused_by_submit_with_its_code_and_a_line(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, printed = _select_verdict(run, _truncated(run["tmp"]))
    assert code == 2 and printed["code"] == "MODEL_OUTPUT_TRUNCATED", printed
    assert printed["fix"] and "флаги" not in printed["fix"] and "--help" not in printed["fix"]
    lines = [json.loads(row) for row in (run["run_root"] / "refusals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["code"] == "MODEL_OUTPUT_TRUNCATED" and row["command"] == "submit" for row in lines), lines


def test_a_missing_or_broken_answer_file_has_a_code_and_a_fix_too(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    ids = ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "context-marker:baseline"]
    code, missing = _api("model-response", *ids, "--artifact", str(run["tmp"] / "nothing.json"))
    assert code == 2 and missing["code"] == "ANSWER_FILE_UNREADABLE" and missing["fix"], missing
    broken = run["tmp"] / "broken.json"
    broken.write_text('{"a": 1,, }', encoding="utf-8")
    code, bad = _api("model-response", *ids, "--artifact", str(broken))
    assert code == 2 and bad["code"] == "ANSWER_JSON_INVALID" and bad["fix"], bad


def test_a_dry_run_leaves_no_refusal_line(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    before = (run["run_root"] / "refusals.jsonl").read_bytes() if (run["run_root"] / "refusals.jsonl").exists() else b""
    code, printed = _select_verdict(run, _truncated(run["tmp"]), "--dry-run")
    assert code == 2 and printed["code"] == "MODEL_OUTPUT_TRUNCATED"
    after = (run["run_root"] / "refusals.jsonl").read_bytes() if (run["run_root"] / "refusals.jsonl").exists() else b""
    assert after == before


# ---- A5-9 -------------------------------------------------------------------------------------------------------------

def test_the_cli_refuse_with_a_code_and_a_fix(tmp_path: Path) -> None:
    missing = tmp_path / ".pilot-runs" / ("a" * 32)
    cases = [
        ("tools.step_size", ["--project", str(tmp_path), "--run", "nothing", "--stage", "context-marker"]),
        ("tools.review_view", ["--project", str(tmp_path), "--run", "nothing", "--candidate", str(tmp_path / "c.json")]),
        ("tools.review_edits", ["--candidate", str(tmp_path / "c.json"), "--edits", str(tmp_path / "e.json")]),
    ]
    for module, argv in cases:
        done = subprocess.run([sys.executable, "-m", module, *argv], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        printed = _last_json(done)
        assert done.returncode == 2 and printed["status"] == "error", (module, printed)
        assert printed.get("code") and printed.get("fix") and printed.get("message"), (module, printed)


# ---- O-9 --------------------------------------------------------------------------------------------------------------

def test_plain_does_not_copy_a_scalar(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy

    from tools import orchestrate_test_case_revision as revision

    value = {"a": ["x", 1, 2.5, True, None, {"b": "y"}], "c": ("z", 3)}
    calls: list[object] = []
    real = copy.deepcopy
    monkeypatch.setattr(revision.copy, "deepcopy", lambda item, *a, **k: calls.append(item) or real(item, *a, **k))
    assert revision._plain(value) == {"a": ["x", 1, 2.5, True, None, {"b": "y"}], "c": ["z", 3]}
    assert calls == []
    receipt = revision._plain(bytearray(b"ab"))  # what is not a scalar or a container is still copied
    assert receipt == bytearray(b"ab") and len(calls) == 1
