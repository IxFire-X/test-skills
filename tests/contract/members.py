"""V0: the files of the smoke between packages — ``python -m pytest -m "contract and not native" -n 4``.

Each file here carries ``pytestmark = pytest.mark.contract`` (``test_contract_set`` keeps the two in step).  The groups are the ones the
architecture review of 2026-11-05 (axis 6) named; a file joins by a line here and a mark there, and leaves the same way — never silently."""
from __future__ import annotations

GROUPS = {
    "counters, registry, manifest": ("test_schema_engine", "test_schema_engine_differential", "test_contract_registry", "test_release_manifest",
                                     "test_pack_profile", "test_dead_modules_removed", "test_projection_drift", "test_ci_gate", "test_no_private_imports", "test_p8b_git",
                                     "test_architecture_map"),
    "budgets of the roles": ("test_review_view", "test_skeleton_budget", "test_p8a2_texts", "test_texts_cases_only", "test_fat3", "test_test_design",
                             "test_context_batches", "test_existing_tests", "test_text_canaries"),
    "form of the CLI": ("test_api_cli", "test_api_error_fields", "test_stages", "test_output_raw", "test_pf1", "test_diagnostics_at_once", "test_run_lock"),
    "classes of the security review": ("test_sec_a", "test_secb", "test_sec_c1", "test_sec_c2"),
    "texts": ("test_cases_import", "test_clear_refusals", "test_documents_only", "test_exec_prepares", "test_generator_skill_operators",
              "test_isolation_contract", "test_partial_automation", "test_pt2", "test_quality_hints", "test_release_ways_out", "test_abandon_closure",
              "test_failure_groups", "test_openspec_change_only"),
}
MEMBERS = tuple(name for group in GROUPS.values() for name in group)

# Tests of the files above that take longer than 30 s: the wall of the slowest case, ``-m "contract and not native" -n 4`` of C4 (26.11), seconds in
# brackets.  The pure checks of the registry and the manifest (contract_registry, release_manifest: 28–33 s of CPU) stay in the fast set.  They run
# apart: ``python -m pytest -m "contract and slow" -n 4`` — before a push that touches the schema engine or mode B.  ``file::function``.
SLOW = (
    "test_partial_automation::test_three_cases_one_blocked_run_two_autotests_and_close_partial",  # 170
    "test_schema_engine_differential::test_the_named_allowance_ecma_end_of_string_is_exercised_and_is_nothing_more",  # 113
    "test_p8b_git::test_foreign_changes_laid_before_the_run_are_there_after_it_and_the_observation_proves_it",  # 100
    "test_quality_hints::test_the_report_counts_weak_assertions_of_the_run_automation",  # 99
    "test_cases_import::test_ready_cases_of_a_csv_become_an_executed_autotest",  # 91
    "test_abandon_closure::test_abandon_is_refused_where_finish_or_ingest_closes",  # 71
    "test_schema_engine_differential::test_artifacts_of_earlier_runs_and_their_mutations",  # 71
    "test_exec_prepares::test_materialize_after_the_static_review_prepares_its_inputs_and_the_ingest_of_the_run_closes",  # 66
    "test_p8b_git::test_a_copy_of_the_mode_b_delivery_finalizes_and_publishes_the_observation_without_the_maintainer_files",  # 63
    "test_exec_prepares::test_materialize_finishes_a_half_done_preparation",  # 62
    "test_cases_import::test_an_empty_test_data_and_objective_are_normalized_in_the_skeleton_and_the_fragment_passes",  # 49
    "test_review_view::test_the_autotest_view_of_the_effective_document_is_written_by_the_code_and_the_cli_has_no_flag_for_it",  # 38
    "test_pt2::test_a_deleted_effective_document_is_a_refusal_not_no_verdict_yet",  # 38
    "test_abandon_closure::test_d3_a_final_failure_of_the_preparation_is_closed_by_the_finish_its_fix_names",  # 38
    "test_cases_import::test_the_skeleton_is_the_last_generator_input_and_the_human_fields_are_checked_before_the_write",  # 38
    "test_cases_import::test_a_case_of_the_import_traces_only_to_its_own_source_requirement",  # 35
    "test_pt2::test_the_report_of_a_run_without_an_effective_document_counts_the_candidate_and_says_so",  # 34
    "test_abandon_closure::test_d3_an_io_failure_of_the_preparation_says_repeat_materialize",  # 33
    "test_pt2::test_a_rejecting_verdict_says_so_instead_of_no_verdict_yet",  # 33
    "test_abandon_closure::test_a_pending_automation_is_closed_by_the_abandon_its_fix_names",  # 33
    "test_abandon_closure::test_b_an_unusable_review_is_closed_by_the_abort_its_fix_names",  # 32
    "test_release_ways_out::test_o2_the_stand_abandon_is_gone_with_the_stand_check_and_an_accepted_chain_is_closed_by_ingest",  # 31
    "test_exec_prepares::test_materialize_after_a_static_review_that_is_not_accepted_materializes_nothing",  # 31
    "test_failure_groups::test_report_keeps_the_method_lines",  # 100 (26.11, the machine loaded; a whole run is built, as test_quality_hints)
    "test_failure_groups::test_the_report_groups_a_failure_by_its_text_and_the_check_of_the_canon",  # 41 (26.11, the machine loaded; a whole run too)
)
