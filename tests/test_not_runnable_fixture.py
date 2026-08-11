import json


def test_incomplete_subscription_fixture_is_honestly_not_runnable(root):
    artifact_root = root / "docs/to_do/e2e/not-runnable"
    scan_result = json.loads(
        (artifact_root / "scan-result.json").read_text(encoding="utf-8")
    )
    run_result = json.loads(
        (artifact_root / "run-result.json").read_text(encoding="utf-8")
    )
    integrity = json.loads(
        (artifact_root / "fixture-integrity.json").read_text(encoding="utf-8")
    )

    assert scan_result["status"] == "error"
    assert scan_result["skillsrc_updated"] is False
    assert any("Манифесты сборки не найдены" in error for error in scan_result["errors"])

    assert run_result["verdict"] == "NOT_RUNNABLE"
    assert run_result["root_cause"]
    assert run_result["stats"] is None

    assert integrity["before"] == integrity["after"]
    assert integrity["before"]["skillsrc_exists"] is False
