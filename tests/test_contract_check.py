import copy

CANONICAL_SKILL_FILES = {
    "context-marker": "skills/context-marker/SKILL.md",
    "tc-generator": "skills/tc-generator/SKILL.md",
    "tc-reviewer": "skills/tc-reviewer/SKILL.md",
    "tc-to-autotest": "skills/tc-to-autotest/SKILL.md",
    "autotest-reviewer": "skills/autotest-reviewer/SKILL.md",
    "orchestrate": "skills/orchestrate/SKILL.md",
}
LEGACY_SKILL_ROOTS = [
    "Разметка контекста",
    "Ручные тест-кейсы",
    "Валидация тест-кейсов",
    "Автоматизированные кейсы на основе тест-кейсов",
    "Валидация автотестов",
    "Оркестратор",
]


def test_skill_files_registry_is_exact_and_steps_have_no_skill_file(contract_check, contract, root):
    """Catches missing or per-step skill paths instead of one portable registry."""
    assert contract["skill_files"] == CANONICAL_SKILL_FILES
    assert all("skill_file" not in step for step in contract["steps"])

    invalid_contract = copy.deepcopy(contract)
    invalid_contract["steps"][0]["skill_file"] = "skills/context-marker/SKILL.md"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("skill_file" in error for error in report["errors"])


def test_registered_skill_files_exist_and_legacy_skill_roots_are_absent(contract, root):
    """Catches portable registry paths that do not match the completed directory move."""
    assert all((root / relative_path).is_file() for relative_path in contract["skill_files"].values())
    assert all(not (root / legacy_root).exists() for legacy_root in LEGACY_SKILL_ROOTS)


def test_tc_to_autotest_requires_validation_report_and_two_canonical_case_branches(contract, contract_check, root):
    """Catches bypassing reviewer evidence or either accepted/corrected case branch."""
    steps = {step["id"]: step for step in contract["steps"]}
    assert set(steps["tc-to-autotest"]["accepts"]) == {
        "validation_report",
        "generated_test_cases",
        "corrected_test_cases",
    }
    assert set(steps["tc-to-autotest"]["forwards"]) == {
        "validation_report",
        "generated_test_cases",
        "corrected_test_cases",
        "generated_test_files",
        "generated_test_methods",
    }

    invalid_contract = copy.deepcopy(contract)
    invalid_contract["steps"][3]["accepts"].remove("validation_report")

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("tc-to-autotest" in error and "validation_report" in error for error in report["errors"])


def test_contract_check_rejects_alias_artifact_in_autotest_stage(contract, contract_check, root):
    """Catches a renamed generated-file artifact entering the automation stage."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["steps"][3]["forwards"].remove("generated_test_files")
    invalid_contract["steps"][3]["forwards"].append("generated_files")

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("tc-to-autotest canonical routing" in error for error in report["errors"])


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


def test_rejects_noncanonical_core_registry(contract_check, contract, root):
    """Catches a near-match core skill being accepted as portable identity."""
    expected_skills = ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
    assert contract["core_skills"] == expected_skills

    invalid_contract = copy.deepcopy(contract)
    invalid_contract["core_skills"][-1] = "orchestrate-lite"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("core skill registry" in error for error in report["errors"])


def test_rejects_noncanonical_persistent_root(contract_check, contract, root):
    """Catches a near-match persistent artifact root being accepted as confined."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["artifact_policy"]["persistent_root"] = "docs/to_do_backup"

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("persistent_root" in error for error in report["errors"])


def test_rejects_nonbaseline_capability(contract_check, contract, root):
    """Catches dropping required Python execution while retaining a portable success claim."""
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["capabilities"][1]["execution"] = False

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("capability baseline" in error for error in report["errors"])


def test_rejects_forwarded_artifact_without_stage_provenance(contract_check, contract, root):
    """Catches forwarding an artifact that the stage neither accepted nor produced."""
    invalid_contract = copy.deepcopy(contract)
    context_marker = invalid_contract["steps"][0]
    context_marker["produces"].remove("source_code_and_diff")
    context_marker["forwards"].append("source_code_and_diff")

    report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

    assert report["status"] == "failed"
    assert any("forwards artifact without provenance" in error for error in report["errors"])


def test_trace_failure_branch_stops_trace_failure(contract_check, contract, root):
    """Catches leaving a failed trace audit without a terminal branch."""
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)

    assert report["status"] == "passed"
    transition = next(
        transition
        for transition in contract["transitions"]
        if transition["from"] == "trace-check" and transition["when"].get("trace_verdict") == "FAIL"
    )
    assert transition == {
        "from": "trace-check",
        "when": {"execution_verdict": "PASS", "trace_verdict": "FAIL"},
        "transform": "stop_trace_failed",
    }


def test_rejects_windows_and_near_match_projection_paths(contract_check, contract, root):
    """Catches Windows-rooted, drive-relative, and noncanonical projection targets."""
    for path in ("\\CONTRACTS.md", "C:CONTRACTS.md", "docs/CONTRACTS.md"):
        invalid_contract = copy.deepcopy(contract)
        invalid_contract["projections"]["contracts"] = path

        report = contract_check.validate_pipeline_contract(invalid_contract, root, check_drift=False)

        assert report["status"] == "failed"
