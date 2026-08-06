import json


def test_doctor_reports_core_and_optional_capabilities(doctor, tmp_path):
    """Catches removing the portable core capability report."""
    report = doctor.inspect_environment(tmp_path)

    assert report["python"]["supported"] is True
    assert report["dependencies"]["jsonschema"]["required"] is True
    assert report["languages"]["typescript"]["execution"] is False


def test_doctor_never_claims_ready_when_required_dependency_is_missing(doctor, monkeypatch, tmp_path):
    """Catches reporting PASS after a required dependency becomes unavailable."""
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)

    report = doctor.inspect_environment(tmp_path)

    assert report["status"] == "NOT_RUNNABLE"


def test_doctor_never_claims_ready_on_unsupported_python(doctor, monkeypatch, tmp_path):
    """Catches reporting PASS when Python is below the 3.10 runtime baseline."""
    monkeypatch.setattr(doctor.sys, "version_info", (3, 9))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())

    report = doctor.inspect_environment(tmp_path)

    assert report["python"]["supported"] is False
    assert report["status"] == "NOT_RUNNABLE"


def test_doctor_rejects_a_missing_pack_root(doctor, tmp_path):
    """Catches a readiness PASS for a path that is not a portable skill pack."""
    report = doctor.inspect_environment(tmp_path / "missing")

    assert report["status"] == "NOT_RUNNABLE"
    assert "root" in report["integrity"]["missing"]


def test_doctor_cli_returns_runtime_error_for_missing_dependency(
    doctor, monkeypatch, tmp_path, capsys
):
    """Catches CLI exit-code-zero success after a required dependency is missing."""
    monkeypatch.setattr(doctor.sys, "argv", ["doctor.py", "--root", str(tmp_path)])
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)

    exit_code = doctor.main()
    report = json.loads(capsys.readouterr().out)

    assert report["status"] == "NOT_RUNNABLE"
    assert exit_code == 2
