"""S1d (security review note 6): the build, the tests and PIT start without secret-like variables.

The model CLI keeps its environment (its credentials are its own business); the driver-log names the
removed variables, never their values.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.live_step5 import Replay
from tools.child_environment import build_environment, removed_names

SECRET = "value-that-must-not-leak"
PRINT_NAMES = [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"]

SOURCE = {
    "PATH": "/usr/bin", "HOME": "/home/u", "JAVA_HOME": "/jdk", "TMP": "/tmp", "TMPDIR": "/tmp", "TEMP": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C",
    "MAVEN_OPTS": "-Xmx1g", "M2_HOME": "/m2", "GRADLE_OPTS": "-Xmx1g", "GRADLE_ENTERPRISE_ACCESS_KEY": "k", "GRADLE_USER_HOME": "/g",
    "HTTP_PROXY": "http://p", "HTTPS_PROXY": "http://p", "NO_PROXY": "localhost", "http_proxy": "http://p", "https_proxy": "http://p", "no_proxy": "x",
    "GITHUB_TOKEN": SECRET, "npm_token": SECRET, "DB_SECRET": SECRET, "DB_PASSWORD": SECRET, "PGPASSWD": SECRET, "OPENAI_API_KEY": SECRET,
    "SERVICE_APIKEY": SECRET, "AWS_ACCESS_KEY_ID": SECRET, "AWS_REGION": "eu", "AZURE_CLIENT_ID": SECRET, "GOOGLE_APPLICATION_CREDENTIALS": "/key.json",
    "TOKENIZERS_PARALLELISM": "false", "USERNAME": "u",
}
REMOVED = ["AWS_ACCESS_KEY_ID", "AWS_REGION", "AZURE_CLIENT_ID", "DB_PASSWORD", "DB_SECRET", "GITHUB_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
           "OPENAI_API_KEY", "PGPASSWD", "SERVICE_APIKEY", "TOKENIZERS_PARALLELISM", "npm_token"]


def test_secret_like_names_are_removed_and_the_build_variables_stay() -> None:
    environment = build_environment(SOURCE)
    assert removed_names(SOURCE) == REMOVED
    assert sorted(set(SOURCE) - set(environment)) == REMOVED
    assert all(environment[name] == SOURCE[name] for name in environment)


def _child_names(run) -> list[str]:
    outcome = run(PRINT_NAMES)
    assert outcome.exit_code == 0, outcome
    return json.loads(outcome.stdout.strip().splitlines()[-1])


def test_the_build_process_never_sees_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_tests import run_subprocess

    monkeypatch.setenv("TS_DEMO_API_TOKEN", SECRET)
    monkeypatch.setenv("MAVEN_OPTS", "-Xmx512m")
    names = _child_names(lambda argv: run_subprocess(argv, tmp_path, timeout=60))
    assert "TS_DEMO_API_TOKEN" not in names and "MAVEN_OPTS" in names and "PATH" in {name.upper() for name in names}


def test_pit_and_its_resolver_never_see_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.mutation import _run

    monkeypatch.setenv("TS_DEMO_SECRET", SECRET)
    names = _child_names(lambda argv: _run(None, argv, tmp_path, 60))
    assert "TS_DEMO_SECRET" not in names


def test_the_driver_log_names_the_removed_variables_without_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TS_DEMO_API_TOKEN", SECRET)
    replay = Replay("2c10d733", tmp_path)
    _code, task = replay.start()
    log = replay.project / ".pilot-runs" / f"{task['run_id']}.driver" / "driver-log.jsonl"
    text = log.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    command = next(row for row in rows if row["event"] == "command")
    assert "TS_DEMO_API_TOKEN" in command["child_env_removed"]
    assert SECRET not in text


def test_the_model_cli_keeps_its_environment() -> None:
    """The model runner is not scrubbed: it never calls the build environment."""
    source = (Path(__file__).resolve().parents[1] / "tools" / "model_runner.py").read_text(encoding="utf-8")
    assert "child_environment" not in source
