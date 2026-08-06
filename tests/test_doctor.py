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
