"""F4b: one entry into the review, the envelope of every CLI, no deepcopy of scalars.

- A3-2: the delivery of cases has one way into the review (``tools.api review-open`` → ``review-select``): the old CLI
  ``tools.orchestrate_test_case_revision`` lives in ``tools/review_legacy.py`` (not in that delivery), and ``session-start``,
  ``review-requested``, ``complete-review`` are not commands there; the build of development keeps all of them;
- A5-5: ``MODEL_OUTPUT_TRUNCATED`` of ``--artifact`` / ``--verdict`` is a refusal with its code and fix, recorded in
  ``refusals.jsonl`` — not ``INVALID_ARGUMENT`` with «check the flags»;
- A5-9: ``context_marker_parts``, ``step_size``, ``review_view``, ``review_edits`` refuse with ``code`` and ``fix``;
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

ROOT = Path(__file__).resolve().parents[1]
LEGACY_COMMANDS = ("session-start", "review-requested", "complete-review")


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("f4b") / "pack", "cases-only")


def _run(pack: Path, module: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8")


def _last_json(done: subprocess.CompletedProcess) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1])


# ---- A3-2 -------------------------------------------------------------------------------------------------------------

def test_the_old_review_cli_is_not_in_the_cases_only_delivery(cases_only_pack: Path) -> None:
    assert not (cases_only_pack / "tools" / "review_legacy.py").exists()
    from tools import pack_registry

    assert "tools/review_legacy.py" in pack_registry.CASES_ONLY_EXCLUDED_FILES


@pytest.mark.parametrize("command", LEGACY_COMMANDS)
def test_the_three_commands_of_the_nine_are_not_commands_of_the_cases_only_delivery(cases_only_pack: Path, command: str) -> None:
    listing = _run(cases_only_pack, "tools.api", "--help").stdout
    assert command not in listing
    refused = _run(cases_only_pack, "tools.api", command, "--help")
    assert refused.returncode == 2 and "invalid choice" in refused.stdout + refused.stderr
    assert _run(cases_only_pack, "tools.api", "review-open", "--help").returncode == 0
    assert _run(cases_only_pack, "tools.api", "review-select", "--help").returncode == 0


def test_the_old_cli_refuses_with_its_code_and_the_way_in_the_cases_only_delivery(cases_only_pack: Path) -> None:
    done = _run(cases_only_pack, "tools.orchestrate_test_case_revision", "publish", "--candidate", "c.json", "--output-dir", "o",
                "--run-root", "r", "--attempt-id", "a" * 32)
    printed = _last_json(done)
    assert done.returncode == 2 and printed["code"] == "COMMAND_NOT_IN_PACKAGE", printed
    assert "review-open" in printed["fix"] and "review-select" in printed["fix"] and "Traceback" not in done.stderr


def test_a_flag_of_the_old_path_in_the_cases_only_delivery_is_a_refusal_with_the_way_in_not_a_traceback(cases_only_pack: Path, tmp_path: Path) -> None:
    start = tmp_path / "start.json"
    start.write_text("{}", encoding="utf-8")
    ids = ["--run-root", str(tmp_path / "run"), "--attempt", "a" * 32, "--output-dir", str(tmp_path / "out")]
    for argv in (["review-open", *ids, "--session-start", str(start)], ["review-select", *ids, "--reviewer-session", str(start)],
                 ["review-select", *ids, "--review", str(start)]):
        done = _run(cases_only_pack, "tools.api", *argv)
        printed = _last_json(done)
        assert done.returncode == 2 and printed["code"] == "LEGACY_REVIEW_NOT_IN_PACKAGE", (argv, printed)
        assert "review-open" in printed["fix"] and "review-select" in printed["fix"] and "Traceback" not in done.stderr


def test_the_development_build_keeps_the_old_path() -> None:
    from tools import api, execution_gate, orchestrate_test_case_revision as revision

    assert execution_gate.legacy_available() is True and (ROOT / "tools" / "review_legacy.py").is_file()
    assert set(LEGACY_COMMANDS) <= set(api.COMMANDS)
    done = subprocess.run([sys.executable, "-m", "tools.orchestrate_test_case_revision", "--help"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert done.returncode == 0 and "publish" in done.stdout and "open-session" in done.stdout
    assert callable(revision.main)


def test_the_gate_loads_only_names_of_its_closed_list() -> None:
    from tools import execution_gate

    assert execution_gate.LEGACY_MODULES == ("review_legacy",)
    with pytest.raises(ValueError):
        execution_gate.load_legacy("pilot_state")


# ---- A5-5 -------------------------------------------------------------------------------------------------------------

def _truncated(tmp_path: Path) -> str:
    path = tmp_path / "cut.json"
    path.write_text('{"schema_version": "5.0.0", "artifacts": {"requirements": [{"id": "SREQ-1", "text": "обор', encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(("command", "flag", "extra"), [("model-response", "--artifact", ["--stage", "context-marker:baseline"]),
                                                       ("review-select", "--verdict", ["--output-dir", "out"])])
def test_a_truncated_answer_is_a_refusal_with_its_code_its_fix_and_a_line_in_the_refusals(tmp_path: Path, command: str, flag: str, extra: list[str]) -> None:
    run = _generated_run(tmp_path)
    ids = ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]
    if "--output-dir" in extra:
        extra = ["--output-dir", str(run["project"] / "bundles")]
    code, printed = _api(command, *ids, *extra, flag, _truncated(tmp_path))
    assert code == 2 and printed["code"] == "MODEL_OUTPUT_TRUNCATED", printed
    assert printed["fix"] and "флаги" not in printed["fix"] and "--help" not in printed["fix"]
    lines = [json.loads(row) for row in (run["run_root"] / "refusals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["code"] == "MODEL_OUTPUT_TRUNCATED" and row["command"] == command for row in lines), lines


def test_a_missing_or_broken_answer_file_has_a_code_and_a_fix_too(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    ids = ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "context-marker:baseline"]
    code, missing = _api("model-response", *ids, "--artifact", str(tmp_path / "nothing.json"))
    assert code == 2 and missing["code"] == "ANSWER_FILE_UNREADABLE" and missing["fix"], missing
    broken = tmp_path / "broken.json"
    broken.write_text('{"a": 1,, }', encoding="utf-8")
    code, bad = _api("model-response", *ids, "--artifact", str(broken))
    assert code == 2 and bad["code"] == "ANSWER_JSON_INVALID" and bad["fix"], bad


def test_a_dry_run_leaves_no_refusal_line(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    before = (run["run_root"] / "refusals.jsonl").read_bytes() if (run["run_root"] / "refusals.jsonl").exists() else b""
    code, printed = _api("review-select", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--output-dir", str(run["project"] / "bundles"),
                         "--verdict", _truncated(tmp_path), "--dry-run")
    assert code == 2 and printed["code"] == "MODEL_OUTPUT_TRUNCATED"
    after = (run["run_root"] / "refusals.jsonl").read_bytes() if (run["run_root"] / "refusals.jsonl").exists() else b""
    assert after == before


# ---- A5-9 -------------------------------------------------------------------------------------------------------------

def test_the_four_cli_refuse_with_a_code_and_a_fix(tmp_path: Path) -> None:
    missing = tmp_path / ".pilot-runs" / ("a" * 32)
    cases = [
        ("tools.context_marker_parts", ["plan", "--run-root", str(missing)]),
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
