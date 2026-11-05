"""V0: the files of the smoke between packages — ``python -m pytest -m "contract and not native" -n 4``.

Each file here carries ``pytestmark = pytest.mark.contract`` (``test_contract_set`` keeps the two in step).  The groups are the ones the
architecture review of 2026-11-05 (axis 6) named; a file joins by a line here and a mark there, and leaves the same way — never silently."""
from __future__ import annotations

GROUPS = {
    "counters, registry, manifest": ("test_schema_engine", "test_schema_engine_differential", "test_contract_registry", "test_release_manifest",
                                     "test_pack_profile", "test_dead_modules_removed", "test_projection_drift", "test_ci_gate", "test_no_private_imports", "test_p8b_git"),
    "budgets of the roles": ("test_review_view", "test_skeleton_budget", "test_p8a2_texts", "test_texts_cases_only", "test_fat3", "test_test_design",
                             "test_context_batches", "test_existing_tests"),
    "form of the CLI": ("test_api_cli", "test_api_error_fields", "test_next_step_commands", "test_output_raw", "test_pf1", "test_diagnostics_at_once"),
    "texts": ("test_cases_import", "test_clear_refusals", "test_documents_only", "test_exec_prepares", "test_generator_skill_operators",
              "test_isolation_contract", "test_partial_automation", "test_pt2", "test_quality_hints", "test_release_ways_out", "test_abandon_closure"),
}
MEMBERS = tuple(name for group in GROUPS.values() for name in group)

# Tests of the files above that take longer than 30 s (measured with ``--durations`` on the machine of V0, seconds in brackets).  They run
# apart: ``python -m pytest -m "contract and slow" -n 4`` — before a push that touches the schema engine or mode B.  ``file::function``.
SLOW = (
    "test_schema_engine_differential::test_the_named_allowance_ecma_end_of_string_is_exercised_and_is_nothing_more",  # 150
    "test_partial_automation::test_three_cases_one_blocked_run_two_autotests_and_close_partial",  # 121
    "test_cases_import::test_ready_cases_of_a_csv_become_an_executed_autotest",  # 101
    "test_exec_prepares::test_exec_after_the_static_review_prepares_its_inputs_and_runs",  # 93
    "test_exec_prepares::test_exec_finishes_a_half_done_preparation",  # 82
    "test_schema_engine_differential::test_artifacts_of_earlier_runs_and_their_mutations",  # 71
    "test_api_cli::test_a_file_written_by_out_makes_no_drift_for_exec",  # 67
    "test_quality_hints::test_the_report_counts_weak_assertions_of_the_run_automation",  # 50
    "test_pt2::test_a_rejecting_verdict_says_so_instead_of_no_verdict_yet",  # 41
    "test_abandon_closure::test_abandon_is_refused_where_finish_or_exec_closes",  # 40
    "test_abandon_closure::test_d3_a_final_failure_of_the_preparation_is_closed_by_the_finish_its_fix_names",  # 35
    "test_abandon_closure::test_b_an_unusable_review_is_closed_by_the_abort_its_fix_names",  # 34
    "test_p8b_git::test_foreign_changes_laid_before_the_run_are_there_after_it_and_the_observation_proves_it",  # 65
)
