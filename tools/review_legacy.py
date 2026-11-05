"""F4b: the old CLI of the review layer — ``publish``, ``open-session``, ``select`` (the nine commands of before P2).

A file of the development build: a delivery of cases does not carry it (``pack_registry.CASES_ONLY_EXCLUDED_FILES``); its one way
into the review is ``tools.api review-open`` then ``review-select``.  ``tools.orchestrate_test_case_revision.main`` loads this module
through ``execution_gate.load_legacy``.  The names of the core are read through ``core.`` — a patch of the core reaches them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools import orchestrate_test_case_revision as core
from tools import output_guard


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [core._diag("", "ORCHESTRATION_ARGUMENT")], "status": "error", "successor_bundle_receipt": None,
                          "message": f"argument error: {message}", "fix": None, "next": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)


_RENAMED = {name: {"code": "COMMAND_RENAMED", "message": f"tools.api {new}"}
            for name, new in (("publish", "review-publish"), ("open-session", "review-open"), ("select", "review-select"))}


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="Publish and read back revision 1 as UNREVIEWED.")
    publish.add_argument("--candidate", required=True, type=Path)
    publish.add_argument("--output-dir", required=True, type=Path)
    publish.add_argument("--run-root", required=True, type=Path)
    publish.add_argument("--attempt-id", required=True)
    publish.add_argument("--csv-profile", default=core._PROFILE)
    publish.add_argument("--out", type=Path, default=None, help="FaT-AU2: write the candidate receipt (--candidate-receipt) to <run>/tmp/<file>")
    open_session = commands.add_parser("open-session", help="Reserve the attempt's only host-owned canonical reviewer session.")
    open_session.add_argument("--candidate", required=True, type=Path)
    open_session.add_argument("--candidate-receipt", required=True, type=Path)
    open_session.add_argument("--assembly-receipt", required=True, type=Path)
    open_session.add_argument("--inventory-receipt", required=True, type=Path)
    open_session.add_argument("--context-receipt", required=True, action="append", type=Path)
    open_session.add_argument("--context-marker-output", required=True, type=Path)
    open_session.add_argument("--generator-fragment", required=True, action="append", type=Path)
    open_session.add_argument("--session-start", required=True, type=Path)
    open_session.add_argument("--run-root", required=True, type=Path)
    open_session.add_argument("--attempt-id", required=True)
    open_session.add_argument("--csv-profile", default=core._PROFILE)
    select = commands.add_parser("select", help="Validate one host-owned reviewer session and select the effective revision.")
    select.add_argument("--candidate", required=True, type=Path)
    select.add_argument("--candidate-receipt", required=True, type=Path)
    select.add_argument("--assembly-receipt", required=True, type=Path)
    select.add_argument("--inventory-receipt", required=True, type=Path)
    select.add_argument("--context-receipt", required=True, action="append", type=Path)
    select.add_argument("--context-marker-output", required=True, type=Path)
    select.add_argument("--generator-fragment", required=True, action="append", type=Path)
    select.add_argument("--reviewer-session", required=True, type=Path)
    select.add_argument("--evidence-receipt", action="append", default=[], type=Path)
    select.add_argument("--review", type=Path)
    select.add_argument("--run-root", required=True, type=Path)
    select.add_argument("--attempt-id", required=True)
    select.add_argument("--output-dir", required=True, type=Path)
    select.add_argument("--csv-profile", default=core._PROFILE)
    args = parser.parse_args(argv)
    if hasattr(args, "csv_profile") and args.csv_profile not in core._PROFILES:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [core._diag("/csv_profile", "ORCHESTRATION_PROFILE")], "status": "error", "successor_bundle_receipt": None,
                          "message": f"unknown csv profile {args.csv_profile}", "fix": f"one of {sorted(core._PROFILES)}", "next": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    try:
        candidate = core.load_json_strict(args.candidate)
        if args.command == "publish":
            from tools.api import _out_target, _write  # the one --out policy and writer of the package

            out = _out_target(str(args.out), args.run_root) if args.out is not None else None  # refused before anything is published
            output_guard.run_output_directory(args.output_dir, args.run_root)
            published = core.publish_command(candidate, args.output_dir, args.csv_profile, run_root=args.run_root, attempt_id=args.attempt_id)
            published["warnings"] = [*published["warnings"], _RENAMED["publish"]]
            if out is not None:
                published["out"] = _write(out, published["candidate_bundle_receipt"])
            print(json.dumps(published, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "select":
            output_guard.run_output_directory(args.output_dir, args.run_root)
        receipt = core._load_receipt(args.candidate_receipt)
        base_contexts = [core.load_json_strict(path) for path in args.context_receipt]
        if args.command == "open-session":
            from tools.pilot_state import publish_context_selection

            base_contexts = [publish_context_selection(args.run_root, args.attempt_id, value) for value in base_contexts]
        else:
            base_contexts = core._read_existing_context_receipts(args.context_receipt, args.run_root, args.attempt_id)
        package = core.reviewer_package_binding(
            candidate,
            receipt,
            core.load_json_strict(args.assembly_receipt),
            core.load_json_strict(args.inventory_receipt),
            base_contexts,
            context_marker_output=core.load_json_strict(args.context_marker_output),
            generator_fragments=[core.load_json_strict(path) for path in args.generator_fragment],
            run_root=args.run_root,
            attempt_id=args.attempt_id,
        )
        if args.command == "open-session":
            boundary = core.open_reviewer_session(args.run_root, args.attempt_id, package, core.load_json_strict(args.session_start))
            print(json.dumps({"package_binding": package, "reviewer_session_boundary": boundary, "status": "opened",
                              "warnings": [_RENAMED["open-session"]]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        result = core.orchestrate_revision(
            candidate,
            receipt,
            package,
            core.load_json_strict(args.reviewer_session),
            None if args.review is None else core.load_json_strict(args.review),
            args.output_dir,
            args.csv_profile,
            run_root=args.run_root,
            attempt_id=args.attempt_id,
            reviewer_evidence_receipts=core._read_existing_context_receipts(args.evidence_receipt, args.run_root, args.attempt_id),
        )
    except core.OrchestrationError as error:
        exit_code = 2 if error.code in {"ORCHESTRATION_PUBLICATION", "ORCHESTRATION_VERIFICATION"} else 1
        print(json.dumps({**core.error_envelope(error), "candidate_bundle_receipt": core._receipt_json(error.candidate_bundle_receipt),
                          "diagnostics": [dict(row) for row in error.diagnostics], "successor_bundle_receipt": core._receipt_json(error.successor_bundle_receipt)},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return exit_code
    except output_guard.OutputRefused as error:  # --out / --output-dir outside the run or the project: its own code and fix
        print(json.dumps({**core.error_envelope(error), "candidate_bundle_receipt": None, "diagnostics": [core._diag("", "ORCHESTRATION_IO")],
                          "successor_bundle_receipt": None, "reason_code": error.code}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    except (core.StrictJsonError, OSError, ValueError) as error:
        # the inner refusal code and its fix; the one envelope, the code is the refusal's own
        envelope = core.error_envelope(error)  # the same reading as tools.api
        reason_code = core.error_code_prefix(envelope["message"])
        print(json.dumps({**envelope, "code": reason_code or "ORCHESTRATION_INPUT", "fix": core._FIXES.get(reason_code) or envelope["fix"],
                          "candidate_bundle_receipt": None, "diagnostics": [core._diag("", "ORCHESTRATION_IO")], "successor_bundle_receipt": None,
                          "reason_code": reason_code}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    output = {**core.select_output(result), "warnings": [_RENAMED["select"]], **core._canary_report(args.run_root, args.attempt_id, None)}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0
