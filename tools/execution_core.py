"""V1 (A3-4): the interface of the core that the execution files (``tools/exec_*.py``) stand on.

``exec_commands`` and ``exec_runner`` take the helpers of the core that have no public name of their
own from here — and from nowhere else: a test (``tests/test_no_private_imports.py``) refuses a private name of another module in them.
Every name below is a public alias of a helper in the module that owns it (``public = _private`` at the end of that module), so
there is one place to change when the owner moves or renames the helper — here and in the alias line, not in each user.

What a package that changes the execution (``materialize``, ``ingest``) may rely on: the names in ``__all__``, their
arguments and what they return.  A change of one of them is a change of this interface and is made here, in the same commit.

Families: ``evidence`` (what a run of tests leaves and how it is read), ``journal`` (the durable state of the run), ``cli`` (the
helpers of the commands), ``closure`` (the closing of the attempt).

This module is part of mode B: a delivery of cases does not carry it, and nothing in such a delivery imports it.
"""

from __future__ import annotations

# evidence — the report of a run of generated tests
from tools.run_tests import (
    attempt_execution_scope,
    authorization_allows_execution,
    changed_file_ids,
    durable_delta_allows_execution,
    durable_execution_artifacts,
    failed_methods,
    post_run_file_digests,
    process_evidence_row,
    records_from_snapshots,
    report_digest_evidence,
    report_snapshots,
    request_is_attempt_local,
    request_report_path,
    runner_diag,
    runner_report,
)

# journal — the durable state of the run: events, receipts, bound files
from tools.pilot_state import (
    canonical_digest,
    publish_fixed_receipt_resuming,
)

# cli — the helpers of ``materialize`` and ``ingest``
from tools.run_pipeline import (
    automation_split_fields,
    baseline_check,
    drift_report,
    print_payload,
    read_run_coordinate,
    report_mismatch,
    style_notes,
)

# closure — the closing of the attempt
from tools.finalize_attempt import accepted_unprepared

__all__ = (
    # evidence
    "attempt_execution_scope", "authorization_allows_execution",
    "changed_file_ids", "durable_delta_allows_execution", "durable_execution_artifacts", "failed_methods",
    "post_run_file_digests", "process_evidence_row", "records_from_snapshots",
    "report_digest_evidence", "report_snapshots", "request_is_attempt_local", "request_report_path",
    "runner_diag", "runner_report",
    # journal
    "canonical_digest", "publish_fixed_receipt_resuming",
    # cli
    "automation_split_fields", "baseline_check", "drift_report", "print_payload", "read_run_coordinate",
    "report_mismatch", "style_notes",
    # closure
    "accepted_unprepared",
)
