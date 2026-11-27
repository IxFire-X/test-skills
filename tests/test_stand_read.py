"""A run whose module tests against a stand tells the generators the name of the address variable, and refuses another name."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_existing_tests import _write
from tests.test_finish import _project
from tools import pilot_state, skillsrc_manifest, stage_steps, step_size
from tools.existing_tests import env_view

STAND = {"service_url_env": "PETCLINIC_URL", "tests_only": True}


def _skillsrc(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    module = {"root": ".", "test": {"level": level}, "service_url_env": "PETCLINIC_URL", "paths": {"context": ["app"]}}
    monkeypatch.setattr(pilot_state, "frozen_skillsrc_matches", lambda *_: True)
    monkeypatch.setattr(skillsrc_manifest, "load_skillsrc", lambda _path: {})
    monkeypatch.setattr(skillsrc_manifest, "normalize_skillsrc", lambda _document: {"modules": [module]})


def test_a_stand_module_gives_the_name_of_its_variable(tmp_path, monkeypatch):
    _skillsrc(monkeypatch, "stand")
    assert step_size.module_stand(tmp_path, tmp_path, {"module": ".", "policy_profile": "local-pilot-v1"}) == STAND
    assert step_size.module_stand(tmp_path, tmp_path, {"module": ".", "policy_profile": "cases-only-v1"}) is None


def test_an_in_process_module_gives_none(tmp_path, monkeypatch):
    _skillsrc(monkeypatch, "in-process")
    assert step_size.module_stand(tmp_path, tmp_path, {"module": ".", "policy_profile": "local-pilot-v1"}) is None


def test_a_batch_at_a_stand_reads_the_variable(tmp_path, monkeypatch):
    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    monkeypatch.setattr(step_size, "module_stand", lambda *_: STAND)
    found = step_size.with_test_env(run_root, {"stage": "tc-generator:BATCH-fixture", "attempt_id": attempt_id, "files": []})
    assert found["stand"]["service_url_env"] == "PETCLINIC_URL" and "name: PETCLINIC_URL" in found["stand"]["rule"]


def test_a_batch_with_another_variable_is_refused_at_a_stand(tmp_path, monkeypatch):
    _project_path, run_root, _attempt_id, _values = _project(tmp_path)
    monkeypatch.setattr(step_size, "module_stand", lambda *_: STAND)
    step = {"step_id": "STEP-B1-001-01", "operation": {"kind": "http", "base_url_source": {"kind": "environment", "name": "BASE_URL"}}}
    with pytest.raises(ValueError, match="STAND_URL_NAME: кейс TC-1, шаг STEP-B1-001-01"):
        stage_steps._assemble(run_root, "tc-generator:BATCH-fixture", {"cases": [{"case_id": "TC-1", "steps": [step]}]}, False, None)


def test_the_test_environment_of_a_module_holds_only_its_tests(tmp_path):
    rows = [_write(tmp_path, "app/src/test/AppTest.java", "import org.springframework.test.web.servlet.MockMvc;\n"),
            _write(tmp_path, "tests/src/test/ApiTest.java", "import java.net.http.HttpClient;\n")]
    view = env_view(tmp_path, {"files": rows}, ["# env"], module="tests").decode("utf-8")
    assert "HttpClient" in view and "MockMvc" not in view
