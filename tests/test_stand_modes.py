"""The insert of the stand mode (W3.2): read only at a stand, small; ingest names the variable when JUnit skipped the stand tests."""
from __future__ import annotations

import pytest

from tools import step_size

ROLES = ("tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


@pytest.mark.parametrize("role", ROLES)
def test_the_stand_insert_is_read_only_in_the_stand_mode(role):
    assert not any("modes" in path.parts for path in step_size.role_files(role))
    assert step_size.role_files(role, ("stand",))[-1].parts[-2:] == ("modes", "stand.md")


@pytest.mark.parametrize("role", ROLES)
def test_a_stand_insert_is_small(role):
    assert step_size.role_files(role, ("stand",))[-1].stat().st_size <= 1600


def test_ingest_names_the_variable_when_the_stand_tests_were_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(step_size, "module_stand", lambda *_: {"service_url_env": "PETCLINIC_URL", "tests_only": False})
    skipped = {"execution_evidence": [{"status": "SKIPPED"}]}
    assert "PETCLINIC_URL" in step_size.stand_skip_note(tmp_path, tmp_path, {}, skipped, "UNKNOWN")["stand_note"]
    assert step_size.stand_skip_note(tmp_path, tmp_path, {}, skipped, "PASS") == {}
