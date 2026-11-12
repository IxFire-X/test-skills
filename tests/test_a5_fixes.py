"""V8 (A5-10): the refusals an agent meets on the way of the cases get a fix, and a bare ``ValueError`` is no code of a refusal."""
from __future__ import annotations

import pytest


# the codes of the cases-only path (generator input, reviewer session, marker parts, bundle publication, review) that had no fix anywhere
CODES = (
    "PLAN_ANSWER_INVALID", "GENERATOR_INPUT_CONFLICT",
    "REVIEWER_SESSION_CLOSED", "REVIEWER_SESSION_OPEN_EVIDENCE", "REVIEWER_EVIDENCE_PATH_UNKNOWN", "REVIEWER_EVIDENCE_DUPLICATE",
    "REVIEWER_ABORT_REASON_INVALID", "REVIEW_UNUSABLE", "REVIEW_CANDIDATE_NOT_PUBLISHED", "REVIEW_ALREADY_REQUESTED",
    "REVIEW_INPUTS_UNAVAILABLE", "CONTEXT_MARKER_PARTS_INCOMPLETE", "CONTEXT_MARKER_REQUIREMENTS_CHANGED",
    "HARDLINK_UNAVAILABLE", "OUTPUT_DIRECTORY_ERROR", "TARGET_INSTALL_FAILED", "TEMPORARY_WRITE_FAILED", "TARGET_READ_FAILED",
    "STEP_INPUT_UNRESOLVED", "INPUT_INVALID",
)


@pytest.mark.parametrize("code", CODES)
def test_the_code_has_a_fix_that_says_what_to_do(code: str) -> None:
    from tools.fixes import fix_for

    fix = fix_for(code)
    assert fix and len(fix) > 20, code


def test_a_value_error_without_a_code_is_input_invalid_with_the_way_out() -> None:
    from tools.json_cli import error_fields

    fields = error_fields(ValueError("missing event journal"))
    assert fields["code"] == "INPUT_INVALID" and fields["message"] == "missing event journal"
    assert "--run-root" in fields["fix"] and "--attempt" in fields["fix"]


def test_a_value_error_with_a_code_and_the_other_exceptions_keep_what_they_had() -> None:
    from tools.json_cli import error_fields

    assert error_fields(ValueError("STAGE_WAITS: the batch waits"))["code"] == "STAGE_WAITS"
    assert error_fields(KeyError("missing"))["code"] == "KeyError"
    assert error_fields(ValueError("x; fix: do this"))["fix"] == "do this"
