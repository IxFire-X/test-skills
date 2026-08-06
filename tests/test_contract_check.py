import copy


def test_pass_transition_is_terminal_not_user_rework(contract_check, contract, root):
    """Catches treating a successful execution verdict as a rework branch."""
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)

    assert report["status"] == "passed"
    transition = next(
        transition
        for transition in contract["transitions"]
        if transition["from"] == "trace-check"
    )
    assert transition["transform"] == "complete"
    assert transition["when"] == {"execution_verdict": "PASS", "trace_verdict": "PASS"}
    run_pass = next(
        transition
        for transition in contract["transitions"]
        if transition["from"] == "run-tests" and transition.get("when", {}).get("execution_verdict") == "PASS"
    )
    assert run_pass["transform"] == "continue_trace_audit"


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


def test_review_verdicts_have_normative_branches(contract_check, contract, root):
    """Catches review verdicts that are listed but cannot control pipeline behavior."""
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)

    assert report["status"] == "passed"
    transitions = {
        (transition["from"], transition["when"].get("review_verdict")): transition["transform"]
        for transition in contract["transitions"]
        if "review_verdict" in transition["when"]
    }
    assert transitions[("tc-reviewer", "ПРИНЯТО")] == "continue_with_original"
    assert transitions[("tc-reviewer", "AUTO_FIX_APPLIED")] == "continue_with_corrected"
    assert transitions[("tc-reviewer", "ТРЕБУЕТ ДОРАБОТКИ")] == "stop_rework"
    assert transitions[("autotest-reviewer", "ПРИНЯТО")] == "continue_with_original"
    assert transitions[("autotest-reviewer", "AUTO_FIX_APPLIED")] == "continue_with_corrected"
    assert transitions[("autotest-reviewer", "ТРЕБУЕТ ДОРАБОТКИ")] == "stop_rework"


def test_rejects_unknown_review_verdict(contract_check, contract, root):
    """Catches accepting a review verdict outside the portable contract."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["verdicts"]["review"][0] = "MAYBE"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("review verdict" in error or "schema:" in error for error in report["errors"])


def test_generated_methods_connect_production_review_execution_and_trace(contract_check, contract, root):
    """Catches a trace chain that lacks generated files or methods as evidence."""
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)

    assert report["status"] == "passed"
    steps = {step["id"]: step for step in contract["steps"]}
    assert {"generated_test_files", "generated_test_methods"} <= set(steps["tc-to-autotest"]["produces"])
    assert {"generated_test_files", "generated_test_methods"} <= set(steps["autotest-reviewer"]["accepts"])
    assert {"generated_test_files", "generated_test_methods"} <= set(steps["run-tests"]["accepts"])
    assert {"generated_test_methods", "execution_evidence"} <= set(steps["trace-check"]["accepts"])


def test_contract_enforces_exact_core_registry_and_policy(contract_check, contract, root):
    """Catches core-skill, capability, and artifact-root near matches accepted as portable."""
    expected_skills = ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
    assert contract["core_skills"] == expected_skills

    invalid_contract = copy.deepcopy(contract)
    invalid_contract["core_skills"][-1] = "orchestrate-lite"
    invalid_contract["artifact_policy"]["persistent_root"] = "docs/to_do_backup"
    invalid_contract["capabilities"][1]["execution"] = False

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("core skill" in error or "persistent_root" in error or "capability" in error or "schema:" in error for error in report["errors"])


def test_rejects_windows_and_near_match_projection_paths(contract_check, contract, root):
    """Catches Windows-rooted, drive-relative, and noncanonical projection targets."""
    for path in ("\\CONTRACTS.md", "C:CONTRACTS.md", "docs/CONTRACTS.md"):
        invalid_contract = copy.deepcopy(contract)
        invalid_contract["projections"]["contracts"] = path

        report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

        assert report["status"] == "failed"
