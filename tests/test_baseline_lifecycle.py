from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixture_factory import canonical_document  # noqa: E402
from tools.baseline_lifecycle import (  # noqa: E402
    ValidatedBaseline,
    advance_baseline,
    build_terminal_run_receipt,
    choose_run_mode,
    validate_baseline_receipt,
)
from tools.build_trace_document import build_trace  # noqa: E402
from tools.canonical_document import document_sha256  # noqa: E402
from tools.flow_artifacts import (  # noqa: E402
    FlowError,
    StoredArtifact,
    artifact_sha256,
    canonical_bytes,
    write_create_only,
)
from tools.orchestrate_test_case_revision import finalize_orchestration  # noqa: E402
from tools.publish_test_case_bundle import Receipt  # noqa: E402
from tools.schema_validation import schema_diagnostics  # noqa: E402
from tools.trace_check import check as check_trace  # noqa: E402


PREFIX_KEYS = (
    "technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt",
    "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting",
    "behavior_context_receipt", "technical_test_classification", "classification_review",
    "effective_technical_evidence", "validation_report",
)
TAIL_KEYS = (
    "effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review",
    "run_result", "trace_document", "trace_audit", "orchestrator_output",
)
BASELINE_ARTIFACT_KEYS = (
    "technical_test_inventory_sha256", "authorized_behavior_sources_sha256",
    "managed_behavior_context_sha256", "behavior_source_accounting_sha256",
    "behavior_context_receipt_sha256", "effective_technical_evidence_sha256",
    "effective_document_sha256", "effective_bundle_receipt_sha256", "trace_document_sha256",
    "trace_audit_sha256", "orchestrator_output_sha256", "terminal_run_receipt_sha256",
)
FINGERPRINT_NAMES = ("pipeline_contract", "policy_bundle", "tool_bundle", "schema_bundle")


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def git(project: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    if completed.returncode:
        raise AssertionError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout.decode("ascii").strip()


def repository_id(project: Path) -> str:
    common = Path(git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not common.is_absolute():
        common = project / common
    return digest(str(common.resolve()))


def make_project(root: Path, name: str = "project") -> tuple[Path, str, str, str]:
    project = root / name
    project.mkdir(parents=True)
    git(project, "init")
    git(project, "config", "user.email", "p6@example.invalid")
    git(project, "config", "user.name", "Pipeline Six")
    (project / "feature.txt").write_text("base\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-m", "base")
    return project, repository_id(project), git(project, "rev-parse", "HEAD"), git(project, "rev-parse", "HEAD^{tree}")


def commit(project: Path, text: str) -> tuple[str, str]:
    (project / "feature.txt").write_text(text + "\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-m", text)
    return git(project, "rev-parse", "HEAD"), git(project, "rev-parse", "HEAD^{tree}")


def valid_schema(value: dict[str, Any], name: str) -> None:
    diagnostics = schema_diagnostics(value, ROOT / "schemas" / name, ROOT)
    if diagnostics:
        raise AssertionError(f"fixture does not validate against {name}: {diagnostics}")


def stored_json(root: Path, relative: PurePosixPath, value: dict[str, Any]) -> StoredArtifact:
    """Materialize an already schema-validated artifact envelope for readback tests."""
    payload = canonical_bytes(value)
    path = root / Path(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return StoredArtifact(path.resolve(), payload, "sha256:" + hashlib.sha256(payload).hexdigest())


def thaw(value: Any) -> Any:
    """Convert recursively frozen production results back to plain JSON for schema fixtures."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def generated_automation(document: dict[str, Any], *, manual_only: bool = False, blocked: bool = False) -> dict[str, Any]:
    source = {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(document)}
    file_digest = digest("def test_item(): pass")
    artifacts: dict[str, Any] = {
        "automation_status": "BLOCKED" if blocked else "GENERATED",
        "source": source,
        "generated_files": [],
        "generated_symbols": [],
        "implementation_relations": [],
        "manual_dispositions": [],
        "diagnostics": [],
    }
    if blocked:
        artifacts["diagnostics"] = [{"path": "/test_cases/0/steps/0/operation", "code": "UNRESOLVED_OPERATION", "message": "Operation cannot be resolved."}]
    elif manual_only:
        artifacts["manual_dispositions"] = [{"case_id": "TC-semantic-fixture", "step_id": "STEP-1"}]
    else:
        artifacts.update({
            "generated_files": [{"file_id": "FILE-item", "path": "tests/test_item.py", "language": "python", "framework": "pytest", "content_digest": file_digest}],
            "generated_symbols": [{"file_id": "FILE-item", "symbol_id": "SYMBOL-item", "locator": {"kind": "python_module_function", "function_name": "test_item"}}],
            "implementation_relations": [
                {"kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
                {"kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "expectation_id": "EXP-1", "assertion_id": "ASSERT-1", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
            ],
        })
    value = {"schema_version": "3.0.0", "stage": "tc-to-autotest", "artifacts": artifacts, "warnings": []}
    valid_schema(value, "tc-to-autotest-output.schema.json")
    return value


def execution(document: dict[str, Any], automation: dict[str, Any], verdict: str) -> dict[str, Any]:
    source = copy.deepcopy(automation["artifacts"]["source"])
    files = {row["file_id"]: row["content_digest"] for row in automation["artifacts"]["generated_files"]}
    evidence = [{
        "run_id": "RUN-current", "source_digest": source["source_digest"], "file_id": row["file_id"],
        "symbol_id": row["symbol_id"], "file_digest": files[row["file_id"]],
        "status": "PASSED" if verdict == "PASS" else "FAILED",
    } for row in automation["artifacts"]["generated_symbols"]]
    not_runnable = verdict == "NOT_RUNNABLE"
    stats = None if not_runnable else {
        "total": len(evidence), "passed": len(evidence) if verdict == "PASS" else 0,
        "failed": len(evidence) if verdict == "FAIL" else 0, "errors": 0, "skipped": 0, "duration_sec": 0,
    }
    value = {
        "schema_version": "3.0.0", "stage": "run-tests", "source": source, "verdict": verdict,
        "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": None},
        "environment": {"status": "missing" if not_runnable else "ready", "interpreter": None if not_runnable else "Python", "interpreter_path": None if not_runnable else "python", "working_dir": ".", "missing": ["runner"] if not_runnable else None},
        "stats": stats, "failed_methods": None, "root_cause": ["runner unavailable"] if not_runnable else None,
        "raw_output_excerpt": None, "ran_at": "2026-08-14T00:00:00+00:00", "exit_code": None if not_runnable else (0 if verdict == "PASS" else 1),
        "run_id": None if not_runnable else "RUN-current", "execution_evidence": [] if not_runnable else evidence,
        "evidence_authoritative": False if not_runnable else True,
        "diagnostics": [{"path": "/target", "code": "RUNNER_MISSING", "message": "Runner is unavailable."}] if not_runnable else [],
    }
    valid_schema(value, "run-tests-output.schema.json")
    return value


def review_artifact(source: dict[str, Any], verdict: str = "ПРИНЯТО") -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        findings = [{"severity": "BLOCKING", "code": "REWORK", "message": "Review requires rework.", "evidence": ["artifact"], "related_ids": ["FILE-item"]}]
    elif verdict == "AUTO_FIX_APPLIED":
        findings = [{"severity": "INFO", "code": "FIXED", "message": "Review applied a fix.", "evidence": ["artifact"], "related_ids": ["FILE-item"]}]
        corrections = [{"id": "FIX-review", "related_ids": ["FILE-item"], "description": "Applied a review correction.", "evidence": ["artifact"]}]
    value = {"schema_version": "3.0.0", "stage": "autotest-reviewer", "artifacts": {"autotest_review": {
        "source": copy.deepcopy(source), "verdict": verdict,
        "reviewed_symbol_pairs": [{"file_id": "FILE-item", "symbol_id": "SYMBOL-item"}],
        "findings": findings, "corrections": corrections,
    }}, "warnings": []}
    valid_schema(value, "autotest-reviewer-output.schema.json")
    return value


def bundle_for(document: dict[str, Any]) -> Receipt:
    return Receipt(
        document["document_id"], document["revision"], "zephyr-scale-step-row-24-v1",
        "feature.json", "feature.md", "feature.csv", document_sha256(document), digest("markdown"), digest("csv"),
    )


def baseline_fingerprints() -> tuple[dict[str, Any], dict[str, str]]:
    registries: dict[str, Any] = {}
    projected: dict[str, str] = {}
    for index, name in enumerate(FINGERPRINT_NAMES):
        files = [{"path": f"contracts/{index}-{name}.json", "sha256": digest(name)}]
        aggregate = artifact_sha256({"files": files})
        registries[name] = {"sha256": aggregate, "files": files}
        projected[name + "_sha256"] = aggregate
    return registries, projected


def prefix_artifacts(
    document: dict[str, Any], repository: str, run_mode: str,
    classification_verdict: str, tc_verdict: str,
    candidate_document: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    inventory = {"module_id": "root", "test_roots": [], "files": [], "symbols": []}
    sources = {"module_id": "root", "sources": []}
    source_envelope = {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {
        "technical_test_inventory": inventory, "technical_test_inventory_sha256": artifact_sha256(inventory),
        "authorized_behavior_sources": sources, "authorized_behavior_sources_sha256": artifact_sha256(sources),
    }, "warnings": []}
    valid_schema(source_envelope, "source-inventory-output.schema.json")
    inventory_sha256 = source_envelope["artifacts"]["technical_test_inventory_sha256"]
    sources_sha256 = source_envelope["artifacts"]["authorized_behavior_sources_sha256"]
    classification = {"schema_version": "1.0.0", "stage": "test-classifier", "artifacts": {"classification": {
        "technical_test_inventory_sha256": inventory_sha256, "classifications": [],
    }}, "warnings": []}
    valid_schema(classification, "test-classifier-output.schema.json")
    classification_sha256 = artifact_sha256(classification["artifacts"]["classification"])
    finding = {"path": "/classifications", "code": "CLASSIFICATION_REWORK", "message": "Classification requires rework."}
    classification_review = {"schema_version": "1.0.0", "stage": "test-classifier-reviewer", "artifacts": {"classification_review": {
        "technical_test_inventory_sha256": inventory_sha256,
        "classification_sha256": classification_sha256, "reviewed_symbol_pairs": [],
        "verdict": classification_verdict, "findings": [] if classification_verdict == "ПРИНЯТО" else [finding],
    }}, "warnings": []}
    valid_schema(classification_review, "test-classifier-reviewer-output.schema.json")
    candidate = document if candidate_document is None else candidate_document
    report = {
        "verdict": tc_verdict,
        "candidate": {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": document_sha256(candidate)},
        "reviewed_case_ids": [document["test_cases"][0]["case_id"]], "findings": [], "corrections": [],
    }
    validation = {"schema_version": "3.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report}, "warnings": []}
    if tc_verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        report["findings"] = [{"severity": "BLOCKING", "code": "REWORK", "message": "Test cases require rework.", "evidence": ["artifact"], "related_ids": [document["test_cases"][0]["case_id"]]}]
    elif tc_verdict == "AUTO_FIX_APPLIED":
        report["findings"] = [{"severity": "INFO", "code": "FIXED", "message": "Test cases were corrected.", "evidence": ["artifact"], "related_ids": [document["test_cases"][0]["case_id"]]}]
        report["corrections"] = [{"id": "FIX-test-case", "related_ids": [document["test_cases"][0]["case_id"]], "description": "Applied a test-case correction.", "evidence": ["artifact"]}]
        validation["artifacts"]["successor_document"] = copy.deepcopy(document)
    valid_schema(validation, "tc-reviewer-output.schema.json")
    context_receipt = {
        "schema_version": "1.0.0", "selected_module": "root",
        "authorized_behavior_sources_sha256": sources_sha256,
        "context_plan_sha256": digest("context-plan"), "batch_result_sha256s": [],
        "fragment_registry": [], "source_outcomes": [],
    }
    valid_schema(context_receipt, "behavior-context-receipt.schema.json")
    context_receipt_sha256 = artifact_sha256(context_receipt)
    managed = {
        "authorized_behavior_sources_sha256": sources_sha256,
        "requirements": copy.deepcopy(document["requirements"]),
        "product_sources": [],
        "requirement_sources": [
            {"requirement_id": row["requirement_id"], "source_ids": ["REQ-local"]}
            for row in document["requirements"]
        ],
    }
    accounting = {
        "authorized_behavior_sources_sha256": sources_sha256,
        "context_receipt_sha256": context_receipt_sha256,
        "source_dispositions": [], "behavior_fragment_groups": [],
    }
    context = {"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {
        "managed_behavior_context": managed, "behavior_source_accounting": accounting,
    }, "warnings": []}
    valid_schema(context, "context-marker-output.schema.json")
    evidence = {
        "technical_test_inventory_sha256": inventory_sha256,
        "technical_test_classification_sha256": classification_sha256,
        "technical_test_review_sha256": artifact_sha256(classification_review["artifacts"]["classification_review"]),
        "files": [], "symbols": [], "classifications": [],
    }
    evidence["effective_technical_evidence_sha256"] = artifact_sha256(evidence)
    valid_schema(evidence, "effective-technical-evidence.schema.json")
    change_scope = {
        "schema_version": "1.0.0", "artifact": "change_scope_receipt",
        "source": {"repository_id": repository, "selected_module": "root", "run_mode": run_mode},
        "payload_sha256": digest("change-scope-payload"),
    }
    changed = {
        "schema_version": "1.0.0", "artifact": "changed-behavior-context",
        "source": {"behavior_context_receipt_sha256": context_receipt_sha256},
        "requirement_ids": sorted(row["requirement_id"] for row in document["requirements"]),
        "retired_requirement_ids": [],
    }
    return {
        "technical_test_inventory": copy.deepcopy(source_envelope),
        "authorized_behavior_sources": copy.deepcopy(source_envelope),
        "change_scope_receipt": change_scope,
        "managed_behavior_context": copy.deepcopy(context),
        "changed_behavior_context": changed,
        "behavior_source_accounting": copy.deepcopy(context),
        "behavior_context_receipt": context_receipt,
        "technical_test_classification": classification,
        "classification_review": classification_review,
        "effective_technical_evidence": evidence,
        "validation_report": validation,
    }


def build_run(
    run_root: Path,
    repository: str,
    target_commit: str,
    target_tree: str,
    *,
    mode: str = "FULL",
    base: tuple[str, str] | None = None,
    input_kind: str | None = None,
    final_status: str = "PASS",
    classification_verdict: str = "ПРИНЯТО",
    tc_verdict: str = "ПРИНЯТО",
    autotest_verdict: str = "ПРИНЯТО",
    source_drift: bool = False,
    ledger_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    document = canonical_document()
    candidate_document: dict[str, Any] | None = None
    if tc_verdict == "AUTO_FIX_APPLIED":
        candidate_document = copy.deepcopy(document)
        document["revision"] = candidate_document["revision"] + 1
        document["parent_sha256"] = document_sha256(candidate_document)
    manual_only = final_status == "MANUAL_ONLY"
    blocked = final_status == "BLOCKED"
    if manual_only:
        step = document["test_cases"][0]["steps"][0]
        step.update({"manual_only": True, "manual_reason": "Physical observation is required.", "operation": None, "inputs": [], "outputs": []})
        step["expectations"][0]["assertions"] = []
    elif final_status == "PASS_WITH_MANUAL_REMAINDER":
        automated = document["test_cases"][0]["steps"][0]
        manual = copy.deepcopy(automated)
        manual.update({"step_id": "STEP-2", "display_order": 2, "manual_only": True, "manual_reason": "Physical observation is required.", "operation": None, "inputs": [], "outputs": []})
        manual["expectations"][0].update({"expectation_id": "EXP-2", "display_order": 1, "assertions": []})
        document["test_cases"][0]["steps"].append(manual)
    elif blocked:
        step = document["test_cases"][0]["steps"][0]
        step.update({"operation": None, "inputs": [], "outputs": [], "automation_blockers": [{"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Operation cannot be resolved.", "provenance": ["artifact"]}]})
    valid_schema(document, "canonical-test-document.schema.json")
    automation = generated_automation(document, manual_only=manual_only, blocked=blocked)
    if final_status == "PASS_WITH_MANUAL_REMAINDER":
        automation["artifacts"]["manual_dispositions"] = [{"case_id": "TC-semantic-fixture", "step_id": "STEP-2"}]
    run_verdict = {"FAIL": "FAIL", "NOT_RUNNABLE": "NOT_RUNNABLE"}.get(final_status, "PASS")
    run = None if manual_only or blocked else execution(document, automation, run_verdict)
    trace = build_trace(document, automation, run)
    valid_schema(trace, "trace-document.schema.json")
    audit = check_trace(trace)
    review = review_artifact(automation["artifacts"]["source"], autotest_verdict)
    pairs = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in automation["artifacts"]["generated_symbols"]]
    review["artifacts"]["autotest_review"]["reviewed_symbol_pairs"] = pairs
    accepted_review = review_artifact(automation["artifacts"]["source"])
    accepted_review["artifacts"]["autotest_review"]["reviewed_symbol_pairs"] = pairs
    orchestration = thaw(finalize_orchestration(document, bundle_for(document), automation, accepted_review, run, trace))
    valid_schema(orchestration, "orchestrator-output.schema.json")
    prefix_values = prefix_artifacts(document, repository, mode, classification_verdict, tc_verdict, candidate_document)
    stored_prefix = {name: write_create_only(run_root, PurePosixPath("artifacts/prefix") / f"{name}.json", value) for name, value in prefix_values.items()}
    tail_values: dict[str, dict[str, Any] | None] = {
        "effective_document": document,
        "effective_bundle_receipt": asdict(bundle_for(document)),
        "automation_artifact": automation,
        "autotest_review": review,
        "run_result": run,
        "trace_document": trace,
        "trace_audit": audit,
        "orchestrator_output": orchestration,
    }
    tails = {name: None if value is None else stored_json(run_root, PurePosixPath("artifacts/tail") / f"{name}.json", value) for name, value in tail_values.items()}
    registries, projected = baseline_fingerprints()
    kind = input_kind or ("git_head" if mode == "FULL" else "git_range")
    if kind == "git_worktree":
        base_identity = base or (target_commit, target_tree)
        change_input = {"input_kind": kind, "repository_id": repository,
                        "base": {"commit": base_identity[0], "tree": base_identity[1]},
                        "target_snapshot_sha256": digest("worktree-snapshot")}
    elif kind == "patch_manifest":
        change_input = {"input_kind": kind, "repository_id": repository,
                        "base_snapshot_sha256": digest("patch-base"),
                        "target_snapshot_sha256": digest("patch-target")}
    else:
        change_input = {"input_kind": kind, "repository_id": repository,
                        "target": {"commit": target_commit, "tree": target_tree}}
        if base is not None:
            change_input["base"] = {"commit": base[0], "tree": base[1]}
    ledger_value = {
        "schema_version": "1.0.0", "artifact": "prefix-ledger", "repository_id": repository,
        "selected_module": "root", "run_mode": mode, "change_input": change_input,
        "analytics_sha256": digest("analytics"), "source_drift": source_drift,
        "fingerprints": registries,
        "artifacts": {name: {"path": stored_prefix[name].path.relative_to(run_root).as_posix(), "sha256": stored_prefix[name].sha256} for name in PREFIX_KEYS},
    }
    if ledger_mutator is not None:
        ledger_mutator(ledger_value)
    ledger = write_create_only(run_root, PurePosixPath("manifest/prefix-ledger.json"), ledger_value)
    return {"ledger": ledger, "tails": tails, "projected": projected, "document": document, "ledger_value": ledger_value}


def persist_terminal(run_root: Path, fixture: dict[str, Any], name: str = "terminal.json") -> StoredArtifact:
    return write_create_only(run_root, PurePosixPath("receipts") / name, build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))


class BaselineLifecycleTests(unittest.TestCase):
    def assert_flow_error(self, code: str, action: Callable[[], Any]) -> FlowError:
        with self.assertRaises(FlowError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def replace_prefix(self, run_root: Path, fixture: dict[str, Any], name: str, value: dict[str, Any]) -> None:
        replacement = stored_json(run_root, PurePosixPath("mutated") / f"{name}.json", value)
        ledger = copy.deepcopy(fixture["ledger_value"])
        ledger["artifacts"][name] = {"path": replacement.path.relative_to(run_root).as_posix(), "sha256": replacement.sha256}
        fixture["ledger"].path.unlink()
        fixture["ledger"] = stored_json(run_root, PurePosixPath("manifest/prefix-ledger.json"), ledger)

    def replace_ledger(self, run_root: Path, fixture: dict[str, Any], ledger: dict[str, Any]) -> None:
        fixture["ledger"].path.unlink()
        fixture["ledger"] = stored_json(run_root, PurePosixPath("manifest/prefix-ledger.json"), ledger)

    def test_terminal_accepts_pass_pass_with_remainder_and_manual_only_no_run(self) -> None:
        """Dropping any eligible branch, especially the valid nullable run branch, breaks terminal authority."""
        for final_status in ("PASS", "PASS_WITH_MANUAL_REMAINDER", "MANUAL_ONLY"):
            with self.subTest(final_status=final_status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); project, identity, head, tree = make_project(root)
                fixture = build_run(root / "run", identity, head, tree, final_status=final_status)
                receipt = build_terminal_run_receipt(fixture["ledger"], fixture["tails"])
                self.assertEqual([], schema_diagnostics(thaw(receipt), ROOT / "schemas/terminal-run-receipt.schema.json", ROOT))
                terminal = write_create_only(root / "run", PurePosixPath("receipts/terminal.json"), receipt)
                self.assertEqual("ADVANCED", advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)["status"])

    def test_terminal_derives_rework_and_failure_gates_from_schema_valid_artifacts(self) -> None:
        """Caller-shaped acceptance must not hide rework, failed execution, or blocked orchestration."""
        cases = (
            {"classification_verdict": "ТРЕБУЕТ ДОРАБОТКИ"}, {"tc_verdict": "ТРЕБУЕТ ДОРАБОТКИ"},
            {"final_status": "FAIL"}, {"final_status": "NOT_RUNNABLE"}, {"final_status": "BLOCKED"},
            {"source_drift": True},
        )
        for index, options in enumerate(cases):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); project, identity, head, tree = make_project(root)
                fixture = build_run(root / "run", identity, head, tree, **options)
                terminal = persist_terminal(root / "run", fixture)
                self.assertIn(advance_baseline({"project": project, "baseline_root": root / f"b{index}"}, terminal)["status"], {"INELIGIBLE", "PROVISIONAL"})

    def test_valid_tc_autofix_and_trace_audit_failure_matrix(self) -> None:
        """A valid test-case successor advances, while audit failure and invalid review/audit shapes do not."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root)
            fixture = build_run(root / "run", identity, head, tree, tc_verdict="AUTO_FIX_APPLIED")
            terminal = persist_terminal(root / "run", fixture)
            self.assertEqual("ADVANCED", advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)["status"])
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); project, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree)
                audit = json.loads(fixture["tails"]["trace_audit"].payload)
                audit["valid"] = False if not invalid else True
                audit["trace_audit"]["verdict"] = "FAIL"
                audit["trace_audit"]["errors"] = ["TRACE_INVALID"]
                audit["errors"] = [{"path": "", "code": "TRACE_INVALID", "message": "Trace input is invalid."}]
                fixture["tails"]["trace_audit"] = stored_json(run_root, PurePosixPath("mutated/audit.json"), audit)
                if invalid:
                    self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
                else:
                    terminal = persist_terminal(run_root, fixture)
                    self.assertEqual("INELIGIBLE", advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)["status"])
        for kind in ("tc_autofix", "classification_findings"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree)
                name = "validation_report" if kind == "tc_autofix" else "classification_review"
                binding = fixture["ledger_value"]["artifacts"][name]
                value = json.loads((run_root / Path(*PurePosixPath(binding["path"]).parts)).read_bytes())
                if kind == "tc_autofix":
                    report = value["artifacts"]["validation_report"]
                    report.update({"verdict": "AUTO_FIX_APPLIED", "findings": [], "corrections": []})
                else:
                    value["artifacts"]["classification_review"]["findings"] = [{"path": "/classifications", "code": "INVALID", "message": "Accepted review has findings."}]
                replacement = stored_json(run_root, PurePosixPath("mutated") / f"{name}.json", value)
                ledger_value = copy.deepcopy(fixture["ledger_value"])
                ledger_value["artifacts"][name] = {"path": replacement.path.relative_to(run_root).as_posix(), "sha256": replacement.sha256}
                if kind == "classification_findings":
                    evidence_binding = ledger_value["artifacts"]["effective_technical_evidence"]
                    evidence = json.loads((run_root / Path(*PurePosixPath(evidence_binding["path"]).parts)).read_bytes())
                    evidence["technical_test_review_sha256"] = artifact_sha256(value["artifacts"]["classification_review"])
                    without_digest = dict(evidence); without_digest.pop("effective_technical_evidence_sha256")
                    evidence["effective_technical_evidence_sha256"] = artifact_sha256(without_digest)
                    evidence_replacement = stored_json(run_root, PurePosixPath("mutated/effective_technical_evidence.json"), evidence)
                    ledger_value["artifacts"]["effective_technical_evidence"] = {"path": evidence_replacement.path.relative_to(run_root).as_posix(), "sha256": evidence_replacement.sha256}
                fixture["ledger"].path.unlink()
                fixture["ledger"] = stored_json(run_root, PurePosixPath("manifest/prefix-ledger.json"), ledger_value)
                self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_terminal_rejects_invalid_autofix_audit_and_cross_source_branches(self) -> None:
        """Schema-invalid auto-fix and disagreeing run/trace/orchestrator sources cannot become receipts."""
        mutations = (
            ("autotest_review", lambda value: value["artifacts"]["autotest_review"].update({"verdict": "AUTO_FIX_APPLIED", "corrections": []})),
            ("trace_audit", lambda value: value["trace_audit"].update({"source_digest": digest("foreign")})),
            ("orchestrator_output", lambda value: value["artifacts"]["orchestration_result"].update({"final_status": "FAIL", "run_verdict": "FAIL"})),
            ("run_result", lambda value: value["source"].update({"source_digest": digest("foreign")})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree)
                stored = fixture["tails"][name]; self.assertIsNotNone(stored)
                value = json.loads(stored.payload); mutate(value)
                replacement = stored_json(run_root, PurePosixPath("mutated") / f"{name}.json", value)
                fixture["tails"][name] = replacement
                self.assert_flow_error("FEATURE_FLOW_INPUT" if name == "autotest_review" else "BASELINE_BINDING", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
        for verdict in ("ТРЕБУЕТ ДОРАБОТКИ", "AUTO_FIX_APPLIED"):
            with self.subTest(autotest_verdict=verdict), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree, autotest_verdict=verdict)
                self.assert_flow_error("BASELINE_BINDING", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_terminal_requires_stored_exact_ledger_and_tail_authority(self) -> None:
        """Plain maps, digest strings, fabricated StoredArtifact fields, foreign roots, and incomplete tails must fail."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger_value"], fixture["tails"]))  # type: ignore[arg-type]
            self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger"].sha256, fixture["tails"]))  # type: ignore[arg-type]
            bad_payload = replace(fixture["ledger"], payload=b"{}")
            self.assert_flow_error("BASELINE_BINDING", lambda: build_terminal_run_receipt(bad_payload, fixture["tails"]))
            foreign = stored_json(root / "foreign", PurePosixPath("tail.json"), json.loads(fixture["tails"]["trace_document"].payload))
            fixture["tails"]["trace_document"] = foreign
            self.assert_flow_error("FLOW_ATOMIC_WRITE", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
        for mutation in ("missing", "extra", "null"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
                if mutation == "missing": del fixture["tails"]["trace_audit"]
                elif mutation == "extra": fixture["tails"]["extra"] = fixture["tails"]["trace_audit"]
                else: fixture["tails"]["trace_audit"] = None
                self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_prefix_ledger_rejects_unsafe_paths_missing_mutated_and_wrong_digests(self) -> None:
        """A ledger path escape, wrong binding, or missing prefix artifact cannot bless filesystem state."""
        mutations = (
            lambda ledger: ledger["artifacts"]["change_scope_receipt"].update({"path": "/absolute.json"}),
            lambda ledger: ledger["artifacts"]["change_scope_receipt"].update({"path": "../escape.json"}),
            lambda ledger: ledger["artifacts"]["change_scope_receipt"].update({"path": "artifacts\\escape.json"}),
            lambda ledger: ledger["artifacts"]["change_scope_receipt"].update({"sha256": digest("wrong")}),
            lambda ledger: ledger["artifacts"].pop("change_scope_receipt"),
            lambda ledger: ledger["artifacts"].update({"extra": next(iter(ledger["artifacts"].values()))}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root)
                fixture = build_run(root / "run", identity, head, tree, ledger_mutator=mutate)
                self.assert_flow_error("FEATURE_FLOW_INPUT" if index in {0, 1, 2, 4, 5} else "BASELINE_BINDING", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            bound = fixture["tails"]["automation_artifact"]; bound.path.write_bytes(bound.payload + b" ")
            self.assert_flow_error("BASELINE_BINDING", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_every_prefix_carrier_is_closed_and_cross_digest_bound(self) -> None:
        """Placeholder context/scope carriers and internally forged effective evidence must never advance."""
        mutations = {
            "change_scope_receipt": lambda value: value.clear(),
            "managed_behavior_context": lambda value: value["artifacts"].pop("managed_behavior_context"),
            "changed_behavior_context": lambda value: value["source"].update({"behavior_context_receipt_sha256": digest("foreign")}),
            "behavior_source_accounting": lambda value: value["artifacts"]["behavior_source_accounting"].update({"context_receipt_sha256": digest("foreign")}),
            "behavior_context_receipt": lambda value: value.update({"selected_module": "foreign"}),
            "effective_technical_evidence": lambda value: value.update({"technical_test_classification_sha256": digest("foreign")}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree)
                # The desired complete prefix must itself be accepted before its one-field mutation.
                build_terminal_run_receipt(fixture["ledger"], fixture["tails"])
                binding = fixture["ledger_value"]["artifacts"][name]
                value = json.loads((run_root / Path(*PurePosixPath(binding["path"]).parts)).read_bytes())
                mutate(value); self.replace_prefix(run_root, fixture, name, value)
                self.assert_flow_error("FEATURE_FLOW_INPUT" if name in {"change_scope_receipt", "managed_behavior_context"} else "BASELINE_BINDING", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_change_input_is_a_closed_mode_bound_repository_union(self) -> None:
        """Extra fields, hybrids, foreign repository IDs, and run-mode mismatches cannot enter terminal receipts."""
        mutations = (
            lambda value: value.update({"extra": True}),
            lambda value: value.update({"base": {"commit": "a" * 40, "tree": "b" * 40}}),
            lambda value: value.update({"repository_id": digest("foreign")}),
            lambda value: value.update({"input_kind": "git_range"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root); run_root = root / "run"
                fixture = build_run(run_root, identity, head, tree)
                ledger = copy.deepcopy(fixture["ledger_value"]); mutate(ledger["change_input"])
                self.replace_ledger(run_root, fixture, ledger)
                self.assert_flow_error("BASELINE_BINDING" if index == 2 else "FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, identity, head, tree = make_project(root)
            fixture = build_run(root / "run", identity, head, tree)
            terminal = thaw(build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))
            terminal["change_input"]["extra"] = True
            self.assertNotEqual([], schema_diagnostics(terminal, ROOT / "schemas/terminal-run-receipt.schema.json", ROOT))

    def test_fingerprint_registries_require_closed_sorted_unique_canonical_aggregation(self) -> None:
        """A label, duplicate, unsorted registry, unsafe path, or fifth fingerprint must not select code authority."""
        def duplicate(ledger: dict[str, Any]) -> None:
            files = ledger["fingerprints"]["tool_bundle"]["files"]; files.append(copy.deepcopy(files[0]))
        def unsorted(ledger: dict[str, Any]) -> None:
            ledger["fingerprints"]["schema_bundle"]["files"] = [{"path": "z", "sha256": digest("z")}, {"path": "a", "sha256": digest("a")}]
        mutations = (
            lambda ledger: ledger["fingerprints"].update({"registry_bundle": copy.deepcopy(ledger["fingerprints"]["tool_bundle"])}),
            lambda ledger: ledger["fingerprints"]["policy_bundle"].update({"sha256": digest("version-label")}),
            duplicate, unsorted,
            lambda ledger: ledger["fingerprints"]["pipeline_contract"]["files"][0].update({"path": "../PIPELINE.md"}),
            lambda ledger: ledger["fingerprints"]["pipeline_contract"].update({"extra": True}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, identity, head, tree = make_project(root)
                fixture = build_run(root / "run", identity, head, tree, ledger_mutator=mutate)
                self.assert_flow_error("FEATURE_FLOW_INPUT" if index == 0 else "BASELINE_FINGERPRINT", lambda: build_terminal_run_receipt(fixture["ledger"], fixture["tails"]))

    def test_all_three_new_schemas_are_closed_in_nested_and_conditional_shapes(self) -> None:
        """Schema closure must reject unknown digest keys and advancement payload/status mismatches."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            terminal = persist_terminal(root / "run", fixture); terminal_value = json.loads(terminal.payload)
            advanced = advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)
            baseline = thaw(advanced["successor_baseline_receipt"])
            schemas_and_values = (
                ("terminal-run-receipt.schema.json", terminal_value, lambda value: value["artifacts"].update({"extra_sha256": digest("extra")})),
                ("feature-baseline-receipt.schema.json", baseline, lambda value: value["artifacts"].update({"extra_sha256": digest("extra")})),
                ("baseline-advancement.schema.json", thaw(advanced), lambda value: value.update({"status": "INELIGIBLE"})),
            )
            for schema, value, mutate in schemas_and_values:
                self.assertEqual([], schema_diagnostics(value, ROOT / "schemas" / schema, ROOT))
                changed = copy.deepcopy(value); mutate(changed)
                self.assertNotEqual([], schema_diagnostics(changed, ROOT / "schemas" / schema, ROOT))

    def test_initial_full_requires_real_clean_exact_head_and_non_git_is_provisional(self) -> None:
        """Declared clean flags, fake hex, dirty trees, foreign repositories, and non-Git paths cannot become durable."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            terminal = persist_terminal(root / "run", fixture)
            self.assertEqual("ADVANCED", advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)["status"])
            (project / "feature.txt").write_text("dirty\n", encoding="utf-8")
            self.assertEqual("PROVISIONAL", advance_baseline({"project": project, "baseline_root": root / "dirty"}, terminal)["status"])
            non_git = root / "plain"; non_git.mkdir()
            self.assertEqual("PROVISIONAL", advance_baseline({"project": non_git, "baseline_root": root / "plain-baselines"}, terminal)["status"])
        for input_kind in ("git_worktree", "patch_manifest"):
            with self.subTest(input_kind=input_kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); project, identity, head, tree = make_project(root)
                fixture = build_run(root / "run", identity, head, tree, input_kind=input_kind)
                self.assertEqual("PROVISIONAL", advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "run", fixture))["status"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root, "one"); foreign, _, _, _ = make_project(root, "two")
            terminal = persist_terminal(root / "run", build_run(root / "run", identity, head, tree))
            self.assertEqual("INELIGIBLE", advance_baseline({"project": foreign, "baseline_root": root / "foreign-baselines"}, terminal)["status"])

    def test_choose_run_mode_requires_exact_validated_baseline_and_fingerprints(self) -> None:
        """Loose receipts and any stale base/module/repository/fingerprint binding force FULL."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            advanced = advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "run", fixture))
            baseline = validate_baseline_receipt(advanced["successor_baseline_receipt"], project, "root", fixture["projected"])
            target = {"input_kind": "git_range", "repository_id": identity, "selected_module": "root", "base": {"commit": head, "tree": tree}, "fingerprints": fixture["projected"]}
            self.assertEqual("CHANGE_SET", choose_run_mode(target, baseline))
            self.assertEqual("FULL", choose_run_mode(target, advanced["successor_baseline_receipt"]))
            for key in ("repository_id", "selected_module", "base", "fingerprints"):
                changed = copy.deepcopy(target)
                if key == "base": changed[key]["tree"] = "f" * 40
                elif key == "fingerprints": changed[key]["tool_bundle_sha256"] = digest("stale")
                else: changed[key] = "foreign"
                self.assertEqual("FULL", choose_run_mode(changed, baseline))

    def test_change_set_requires_exact_predecessor_base_head_and_real_objects(self) -> None:
        """A forged/missing base or nonexistent/wrong head tree cannot extend the baseline chain."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, base, base_tree = make_project(root)
            first_fixture = build_run(root / "first", identity, base, base_tree)
            first = advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "first", first_fixture))
            predecessor = validate_baseline_receipt(first["successor_baseline_receipt"], project, "root", first_fixture["projected"])
            head, head_tree = commit(project, "head")
            good = build_run(root / "next", identity, head, head_tree, mode="CHANGE_SET", base=(base, base_tree))
            self.assertEqual("ADVANCED", advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "next", good), predecessor)["status"])
            for index, (bad_base, bad_tree) in enumerate(((("f" * 40), base_tree), (base, "e" * 40))):
                fixture = build_run(root / f"bad{index}", identity, head, head_tree, mode="CHANGE_SET", base=(bad_base, bad_tree))
                self.assertEqual("INELIGIBLE", advance_baseline({"project": project, "baseline_root": root / f"bad-baselines{index}"}, persist_terminal(root / f"bad{index}", fixture), predecessor)["status"])
            missing = build_run(root / "missing", identity, head, head_tree, mode="CHANGE_SET")
            self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: persist_terminal(root / "missing", missing))
            nonexistent = build_run(root / "nonexistent", identity, "d" * 40, "c" * 40, mode="CHANGE_SET", base=(base, base_tree))
            self.assertEqual("INELIGIBLE", advance_baseline({"project": project, "baseline_root": root / "nonexistent-baselines"}, persist_terminal(root / "nonexistent", nonexistent), predecessor)["status"])
            loose = first["successor_baseline_receipt"]
            self.assertEqual("INELIGIBLE", advance_baseline({"project": project, "baseline_root": root / "loose-baselines"}, persist_terminal(root / "next", good, "loose.json"), loose)["status"])
            def stale_fingerprint(ledger: dict[str, Any]) -> None:
                files = [{"path": "contracts/stale.json", "sha256": digest("stale")}]
                ledger["fingerprints"]["tool_bundle"] = {"files": files, "sha256": artifact_sha256({"files": files})}
            stale = build_run(root / "stale", identity, head, head_tree, mode="CHANGE_SET", base=(base, base_tree), ledger_mutator=stale_fingerprint)
            self.assertEqual("INELIGIBLE", advance_baseline({"project": project, "baseline_root": root / "stale-baselines"}, persist_terminal(root / "stale", stale), predecessor)["status"])

    def test_change_set_rejects_same_repository_head_not_descended_from_predecessor(self) -> None:
        """A committed sibling head in the same repository must not create a baseline receipt or link."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, root_commit, _ = make_project(root)
            predecessor_commit, predecessor_tree = commit(project, "predecessor")
            first = build_run(root / "first", identity, predecessor_commit, predecessor_tree)
            advanced = advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "first", first))
            predecessor = validate_baseline_receipt(advanced["successor_baseline_receipt"], project, "root", first["projected"])
            sibling = root / "sibling"
            git(project, "worktree", "add", "-b", "unrelated-successor", str(sibling), root_commit)
            sibling_commit, sibling_tree = commit(sibling, "sibling")
            fixture = build_run(root / "sibling-run", identity, sibling_commit, sibling_tree, mode="CHANGE_SET", base=(predecessor_commit, predecessor_tree))
            before = sorted(path.relative_to(root / "baselines").as_posix() for path in (root / "baselines").rglob("*") if path.is_file())
            result = advance_baseline({"project": sibling, "baseline_root": root / "baselines"}, persist_terminal(root / "sibling-run", fixture), predecessor)
            after = sorted(path.relative_to(root / "baselines").as_posix() for path in (root / "baselines").rglob("*") if path.is_file())
            self.assertEqual("INELIGIBLE", result["status"])
            self.assertEqual(before, after)

    def test_linked_worktree_identity_and_real_competing_successor_conflict(self) -> None:
        """Common Git identity must span worktrees while one predecessor rejects a distinct committed successor."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, base, base_tree = make_project(root)
            first_fixture = build_run(root / "first", identity, base, base_tree)
            first = advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "first", first_fixture))
            predecessor = validate_baseline_receipt(first["successor_baseline_receipt"], project, "root", first_fixture["projected"])
            branch_a = root / "branch-a"; branch_b = root / "branch-b"
            git(project, "worktree", "add", "-b", "successor-a", str(branch_a), base)
            git(project, "worktree", "add", "-b", "successor-b", str(branch_b), base)
            a_commit, a_tree = commit(branch_a, "successor a"); b_commit, b_tree = commit(branch_b, "successor b")
            self.assertEqual(identity, repository_id(branch_a)); self.assertEqual(identity, repository_id(branch_b))
            a = build_run(root / "a-run", identity, a_commit, a_tree, mode="CHANGE_SET", base=(base, base_tree))
            b = build_run(root / "b-run", identity, b_commit, b_tree, mode="CHANGE_SET", base=(base, base_tree))
            self.assertEqual("ADVANCED", advance_baseline({"project": branch_a, "baseline_root": root / "baselines"}, persist_terminal(root / "a-run", a), predecessor)["status"])
            self.assertEqual("BASELINE_CONFLICT", advance_baseline({"project": branch_b, "baseline_root": root / "baselines"}, persist_terminal(root / "b-run", b), predecessor)["status"])
            self.assertFalse(any(path.name in {"latest", "latest.json", "current", "current.json"} for path in (root / "baselines").rglob("*")))

    def test_concurrent_identical_advancement_is_idempotent_and_read_back(self) -> None:
        """Two identical writers must yield exactly one ADVANCED and one readback-proved IDEMPOTENT result."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            terminal = persist_terminal(root / "run", fixture); statuses: list[str] = []; failures: list[BaseException] = []
            def one() -> None:
                try: statuses.append(advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)["status"])
                except BaseException as error: failures.append(error)
            threads = [threading.Thread(target=one) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual([], failures)
            self.assertCountEqual(["ADVANCED", "IDEMPOTENT"], statuses)

    def test_baseline_persistence_rejects_symlinked_receipt_directory(self) -> None:
        """Receipt persistence must inherit Task 1 confinement and symlink traversal rejection."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root)
            fixture = build_run(root / "run", identity, head, tree); terminal = persist_terminal(root / "run", fixture)
            baseline_root = root / "baselines"; outside = root / "outside"; baseline_root.mkdir(); outside.mkdir()
            try:
                (baseline_root / "receipts").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                if sys.platform != "win32":
                    self.skipTest(f"symlink creation unavailable: {error}")
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(baseline_root / "receipts"), str(outside)],
                    capture_output=True, check=False,
                )
                if junction.returncode:
                    self.skipTest(f"symlink and junction creation unavailable: {error}")
            self.assert_flow_error("FLOW_ATOMIC_WRITE", lambda: advance_baseline({"project": project, "baseline_root": baseline_root}, terminal))
            self.assertEqual([], list(outside.iterdir()))

    def test_receipts_and_validated_baselines_are_recursively_immutable_and_safe(self) -> None:
        """Nested mutation and hostile diagnostic values must not alter accepted state or leak through tracebacks."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            receipt = build_terminal_run_receipt(fixture["ledger"], fixture["tails"])
            self.assertIsInstance(receipt, MappingProxyType)
            with self.assertRaises(TypeError): receipt["acceptance"]["final_status"] = "FAIL"
            advanced = advance_baseline({"project": project, "baseline_root": root / "baselines"}, write_create_only(root / "run", PurePosixPath("receipts/terminal.json"), receipt))
            baseline = validate_baseline_receipt(advanced["successor_baseline_receipt"], project, "root", fixture["projected"])
            with self.assertRaises(TypeError): baseline.receipt["artifacts"]["effective_document_sha256"] = digest("mutated")
            seeded = "credential-value-must-not-appear"
            error = self.assert_flow_error("FEATURE_FLOW_INPUT", lambda: build_terminal_run_receipt({"seed": seeded}, fixture["tails"]))  # type: ignore[arg-type]
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn(seeded, rendered)


if __name__ == "__main__":
    unittest.main()
