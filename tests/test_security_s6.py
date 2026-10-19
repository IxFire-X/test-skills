"""S6 of the security fixes: the model CLI gets an allowlisted environment, not the whole one of the driver.

By default only PATH, HOME, LANG/LC_*, TMP*/TEMP, JAVA_HOME (and the minimum Windows needs to start a process) reach
the CLI; anything else (keys of the CLI itself, a proxy) only by name in ``.skillsrc`` ``review_runner.env_passthrough``.
The names passed are written to the driver-log and the runner result, never the values.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from tests.test_model_runner import SavedModel, _start_runner, _until_done
from tools import model_runner

SECRET = "s6-secret-value-0123456789"
DUMP = [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"]


def test_the_allowlist_keeps_what_a_process_needs_and_drops_the_rest() -> None:
    source = {"PATH": "/bin", "HOME": "/home/u", "LANG": "ru_RU.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp", "TEMP": "/tmp", "JAVA_HOME": "/jdk",
              "MY_API_TOKEN": SECRET, "AWS_SECRET_ACCESS_KEY": SECRET, "HTTPS_PROXY": "http://proxy:3128", "GIT_ASKPASS": "x"}
    env, names = model_runner.cli_environment(source)
    assert set(env) == {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "JAVA_HOME"} and names == sorted(env)
    env, names = model_runner.cli_environment(source, passthrough=("HTTPS_PROXY", "MY_API_TOKEN", "ABSENT"))
    assert {"HTTPS_PROXY", "MY_API_TOKEN"} <= set(env) and "ABSENT" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    prefixed, _names = model_runner.cli_environment({**source, "GIGACODE_URL": "u", "GIGACODE_KEY": "k", "GIGA": "g"}, passthrough=("GIGACODE_*", "HTTP*"))
    assert {"GIGACODE_URL", "GIGACODE_KEY", "HTTPS_PROXY"} <= set(prefixed) and "GIGA" not in prefixed and "MY_API_TOKEN" not in prefixed
    assert model_runner.withheld_names(source) == ["AWS_SECRET_ACCESS_KEY", "GIT_ASKPASS", "HTTPS_PROXY", "MY_API_TOKEN"]
    windows, _names = model_runner.cli_environment({"SystemRoot": "C:\\Windows", "USERPROFILE": "C:\\Users\\u", "Path": "C:\\bin", "APPDATA": "x",
                                                    "OneDrive": "y"})
    assert set(windows) == {"SystemRoot", "USERPROFILE", "Path", "APPDATA"}  # Windows names are case-insensitive


def _launch(directory: Path, config: model_runner.RunnerConfig) -> list[str]:
    model_runner.launch(directory, list(DUMP), "x", timeout=60, digest="sha256:" + "0" * 64, passthrough=config.env_passthrough)
    deadline = time.monotonic() + 60
    while model_runner.state(directory) != "done":
        assert time.monotonic() < deadline
        time.sleep(0.2)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    seen = json.loads((directory / "stdout.txt").read_text(encoding="utf-8"))
    assert result["env_names"] == seen  # exactly the names the CLI saw
    assert SECRET not in (directory / "spec.json").read_text(encoding="utf-8") + (directory / "result.json").read_text(encoding="utf-8")
    return seen


def test_a_token_in_the_environment_does_not_reach_the_cli_without_passthrough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TS_S6_API_TOKEN", SECRET)
    seen = _launch(tmp_path / "plain", model_runner.configure({}, preset="claude"))
    assert "TS_S6_API_TOKEN" not in seen and "TEST_SKILLS_RUNNER_LAUNCH" not in seen
    passed = _launch(tmp_path / "passed", model_runner.configure({"review_runner": {"preset": "claude", "env_passthrough": ["TS_S6_API_TOKEN"]}}))
    assert "TS_S6_API_TOKEN" in passed


def test_passthrough_names_are_checked(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator

    for bad in (["BAD NAME"], ["HTTP*_PROXY"], ["*"], "GIGACODE_*"):  # a pattern has its only `*` at the end
        with pytest.raises(model_runner.RunnerError):
            model_runner.configure({"review_runner": {"preset": "claude", "env_passthrough": bad}})
    assert model_runner.configure({"review_runner": {"preset": "claude", "env_passthrough": ["GIGACODE_*"]}}).env_passthrough == ("GIGACODE_*",)
    schema = json.loads((Path(model_runner.ROOT) / "schemas" / "skillsrc.schema.json").read_text(encoding="utf-8"))
    runner = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], **schema["$defs"]["review_runner"]}
    assert Draft202012Validator(runner).is_valid({"preset": "claude", "env_passthrough": ["HTTPS_PROXY"]})
    assert not Draft202012Validator(runner).is_valid({"preset": "claude", "env_passthrough": ["a=b"]})


def test_the_driver_log_names_the_variables_passed_to_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO_TOKEN", SECRET)
    project, task = _start_runner(tmp_path)
    done, _waits = _until_done(project, task, SavedModel("cases-only-v1"))
    driver_dir = project / ".pilot-runs" / (done["run_id"] + ".driver")
    log = (driver_dir / "driver-log.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in log.splitlines() if '"runner_launched"' in line]
    assert rows and all("PATH" in {name.upper() for name in row["env_names"]} and "FOO_TOKEN" not in row["env_names"]
                        and "FOO_TOKEN" in row["env_withheld"] for row in rows)  # withheld, by name only
    results = [json.loads((Path(row["directory"]) / "result.json").read_text(encoding="utf-8")) for row in rows if (Path(row["directory"]) / "result.json").is_file()]
    assert results and all("FOO_TOKEN" not in result["env_names"] for result in results)
    assert SECRET not in log
