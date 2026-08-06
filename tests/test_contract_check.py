import copy


def test_pass_transition_is_terminal_not_user_rework(contract_check, contract, root):
    """Catches treating a successful execution verdict as a rework branch."""
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)

    assert report["status"] == "passed"
    transition = next(
        transition
        for transition in contract["transitions"]
        if transition.get("when", {}).get("execution_verdict") == "PASS"
    )
    assert transition["transform"] == "complete"


def test_rejects_unknown_terminal_transform(contract_check, contract, root):
    """Catches accepting a terminal verdict branch with unsupported behavior."""
    invalid_contract = copy.deepcopy(contract)
    transition = next(
        transition
        for transition in invalid_contract["transitions"]
        if transition.get("when", {}).get("execution_verdict") == "FAIL"
    )
    transition["transform"] = "retry_forever"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("terminal transform" in error for error in report["errors"])


def test_rejects_projection_outside_declared_root(contract_check, contract, root):
    """Catches projection declarations that escape the portable pack root."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["projections"]["contracts"] = "../CONTRACTS.md"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("projection path" in error for error in report["errors"])


def test_rejects_step_artifact_not_declared_by_contract(contract_check, contract, root):
    """Catches a pipeline step referring to an artifact outside the contract registry."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["steps"][0]["produces"] = ["unknown_artifact"]

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("unknown artifact" in error for error in report["errors"])
