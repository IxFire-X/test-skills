from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixture_factory import canonical_document
from tools.canonical_document import document_sha256
from tools.automation_validation import validate_automation_artifact
from tools.schema_validation import schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]


def source_for(document: dict) -> dict:
    return {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(document)}


def generated(document: dict) -> dict:
    content = b"def test_item(): pass\n"
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return {
        "schema_version": "3.0.0", "stage": "tc-to-autotest", "warnings": [],
        "artifacts": {
            "automation_status": "GENERATED", "source": source_for(document),
            "generated_files": [{"file_id": "FILE-item", "path": "tests/test_item.py", "language": "python", "framework": "pytest", "content_digest": digest}],
            "generated_symbols": [{"file_id": "FILE-item", "symbol_id": "SYMBOL-item", "locator": {"kind": "python_module_function", "function_name": "test_item"}}],
            "implementation_relations": [
                {"kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
                {"kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "expectation_id": "EXP-1", "assertion_id": "ASSERT-1", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
            ],
            "manual_dispositions": [], "diagnostics": [],
        },
    }


def run_result(document: dict, artifact: dict, verdict: str = "PASS") -> dict:
    source = source_for(document)
    digests = {row["file_id"]: row["content_digest"] for row in artifact["artifacts"]["generated_files"]}
    evidence = [{"run_id": "RUN-current", "source_digest": source["source_digest"], "file_id": row["file_id"], "symbol_id": row["symbol_id"], "file_digest": digests[row["file_id"]], "status": "PASSED"} for row in artifact["artifacts"]["generated_symbols"]]
    return {
        "schema_version": "3.0.0", "stage": "run-tests", "source": source, "verdict": verdict,
        "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": None},
        "environment": {"status": "ready", "interpreter": "Python", "interpreter_path": "python", "working_dir": ".", "missing": None},
        "stats": {"total": len(evidence), "passed": len(evidence), "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0},
        "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": "2026-08-12T00:00:00+00:00", "exit_code": 0,
        "run_id": "RUN-current", "execution_evidence": evidence, "evidence_authoritative": True, "diagnostics": [],
    }


def manual_document() -> dict:
    document = canonical_document()
    step = document["test_cases"][0]["steps"][0]
    step.update({"manual_only": True, "manual_reason": "Synthetic physical check.", "operation": None, "inputs": [], "outputs": []})
    step["expectations"][0]["assertions"] = []
    return document


def blocked_document() -> dict:
    document = canonical_document()
    step = document["test_cases"][0]["steps"][0]
    step.update({"operation": None, "inputs": [], "outputs": [], "automation_blockers": [{"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Synthetic unresolved operation.", "provenance": ["https://example.invalid/context"]}]})
    return document


def blocked_automation(document: dict) -> dict:
    artifact = generated(document)
    artifact["artifacts"].update({"automation_status": "BLOCKED", "generated_files": [], "generated_symbols": [], "implementation_relations": [], "manual_dispositions": [], "diagnostics": [{"path": "/test_cases/0/steps/0/operation", "code": "UNRESOLVED_OPERATION", "message": "Synthetic unresolved operation."}]})
    return artifact


class BuildTraceV3Tests(unittest.TestCase):
    def assert_nominal(self, document: dict, automation: dict, run: dict | None = None) -> None:
        self.assertEqual([], validate_automation_artifact(automation, document))
        if run is not None:
            self.assertEqual([], schema_diagnostics(run, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))

    def test_builds_atomic_trace_without_cartesian_requirement_symbol_rows(self) -> None:
        from tools.build_trace_document import build_trace

        document = canonical_document()
        automation = generated(document)
        self.assert_nominal(document, automation, run_result(document, automation))
        trace = build_trace(document, automation, run_result(document, automation))
        self.assertEqual("3.0.0", trace["schema_version"])
        self.assertEqual("trace", trace["stage"])
        self.assertEqual(automation["artifacts"]["implementation_relations"], trace["implementation_relations"])
        self.assertEqual("PASS", trace["final_verdict"])
        self.assertEqual(1, len(trace["files"]))
        self.assertEqual(1, len(trace["symbols"]))

    def test_manual_and_blocked_branches_require_no_run(self) -> None:
        from tools.build_trace_document import TraceBuildError, build_trace

        manual = manual_document()
        manual_artifact = generated(manual)
        manual_artifact["artifacts"].update({"generated_files": [], "generated_symbols": [], "implementation_relations": [], "manual_dispositions": [{"case_id": "TC-semantic-fixture", "step_id": "STEP-1"}]})
        self.assert_nominal(manual, manual_artifact)
        self.assertEqual("MANUAL_ONLY", build_trace(manual, manual_artifact, None)["final_verdict"])

        blocked = blocked_document()
        blocked_artifact = blocked_automation(blocked)
        self.assert_nominal(blocked, blocked_artifact)
        self.assertEqual("BLOCKED", build_trace(blocked, blocked_artifact, None)["final_verdict"])
        with self.assertRaises(TraceBuildError):
            build_trace(manual, manual_artifact, run_result(manual, generated(canonical_document())))

    def test_final_verdict_matrix_for_mixed_fail_and_not_runnable_runs(self) -> None:
        from tools.build_trace_document import build_trace

        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        cases = []
        failed = run_result(document, automation, "FAIL")
        failed["execution_evidence"][0]["status"] = "FAILED"
        failed["stats"].update({"passed": 0, "failed": 1})
        cases.append(("FAIL", document, automation, failed))
        runtime_nr = run_result(document, automation, "NOT_RUNNABLE")
        runtime_nr["execution_evidence"][0]["status"] = "SKIPPED"
        runtime_nr["stats"].update({"passed": 0, "skipped": 1})
        cases.append(("NOT_RUNNABLE", document, automation, runtime_nr))
        prestart = run_result(document, automation, "NOT_RUNNABLE")
        prestart.update({"run_id": None, "execution_evidence": [], "evidence_authoritative": False, "stats": None, "exit_code": None, "diagnostics": [{"path": "/x", "code": "X", "message": "safe"}]})
        cases.append(("NOT_RUNNABLE", document, automation, prestart))
        mixed = canonical_document(2)
        manual_step = mixed["test_cases"][0]["steps"][1]
        manual_step.update({"manual_only": True, "manual_reason": "Synthetic remainder.", "operation": None, "inputs": [], "outputs": []})
        manual_step["expectations"][0]["assertions"] = []
        mixed_auto = generated(mixed)
        mixed_auto["artifacts"]["manual_dispositions"] = [{"case_id": "TC-semantic-fixture", "step_id": "STEP-2"}]
        cases.append(("PASS_WITH_MANUAL_REMAINDER", mixed, mixed_auto, run_result(mixed, mixed_auto)))
        for expected, doc, artifact, run in cases:
            with self.subTest(expected=expected):
                self.assert_nominal(doc, artifact, run)
                self.assertEqual(expected, build_trace(doc, artifact, run)["final_verdict"])

    def test_pair_identity_and_evidence_matrix_rejects_bad_current_evidence(self) -> None:
        from tools.build_trace_document import TraceBuildError, build_trace

        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        base = run_result(document, automation)
        self.assert_nominal(document, automation, base)
        cases = {
            "source": lambda value: value["source"].__setitem__("source_digest", "sha256:" + "0" * 64),
            "evidence-source": lambda value: value["execution_evidence"][0].__setitem__("source_digest", "sha256:" + "0" * 64),
            "digest": lambda value: value["execution_evidence"][0].__setitem__("file_digest", "sha256:" + "0" * 64),
            "wrong-pair": lambda value: value["execution_evidence"][0].__setitem__("symbol_id", "SYMBOL-other"),
            "duplicate": lambda value: value["execution_evidence"].append(copy.deepcopy(value["execution_evidence"][0])),
            "conflict": lambda value: value["execution_evidence"].append({**value["execution_evidence"][0], "status": "FAILED"}),
            "missing": lambda value: value.__setitem__("execution_evidence", []),
            "skipped": lambda value: value["execution_evidence"][0].__setitem__("status", "SKIPPED"),
            "error": lambda value: value["execution_evidence"][0].__setitem__("status", "ERROR"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(base)
                mutate(candidate)
                if name in {"skipped", "error"}:
                    candidate["verdict"] = "NOT_RUNNABLE" if name == "skipped" else "FAIL"
                    self.assertIn(build_trace(document, automation, candidate)["final_verdict"], {"NOT_RUNNABLE", "FAIL"})
                else:
                    with self.assertRaises(TraceBuildError):
                        build_trace(document, automation, candidate)
        stale = copy.deepcopy(base)
        stale["execution_evidence"][0]["run_id"] = "RUN-old"
        stale.update({"verdict": "NOT_RUNNABLE", "evidence_authoritative": False})
        self.assertEqual("NOT_RUNNABLE", build_trace(document, automation, stale)["final_verdict"])

    def test_multi_symbol_and_same_local_symbol_remain_distinct_pairs(self) -> None:
        from tools.build_trace_document import build_trace

        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        file = copy.deepcopy(automation["artifacts"]["generated_files"][0])
        file.update({"file_id": "FILE-second", "path": "tests/test_second.py"})
        automation["artifacts"]["generated_files"].append(file)
        symbol = copy.deepcopy(automation["artifacts"]["generated_symbols"][0])
        symbol.update({"file_id": "FILE-second", "locator": {"kind": "python_module_function", "function_name": "test_second"}})
        automation["artifacts"]["generated_symbols"].append(symbol)
        copied = []
        for relation in copy.deepcopy(automation["artifacts"]["implementation_relations"]):
            relation["file_id"] = "FILE-second"
            copied.append(relation)
        automation["artifacts"]["implementation_relations"] = [
            automation["artifacts"]["implementation_relations"][0], copied[0],
            automation["artifacts"]["implementation_relations"][1], copied[1],
        ]
        self.assert_nominal(document, automation, run_result(document, automation))
        trace = build_trace(document, automation, run_result(document, automation))
        self.assertEqual({("FILE-item", "SYMBOL-item"), ("FILE-second", "SYMBOL-item")}, {(row["file_id"], row["symbol_id"]) for row in trace["symbols"]})
        self.assertEqual(4, len(trace["implementation_relations"]))

    def test_v21_and_immutable_safe_diagnostics_and_cli_are_enforced(self) -> None:
        from tools.build_trace_document import TraceBuildError, build_trace

        with self.assertRaises(TraceBuildError) as raised:
            build_trace({"schema_version": "2.1.0", "secret": "TOPSECRET"}, {}, None)
        self.assertNotIn("TOPSECRET", str(raised.exception))
        with self.assertRaises(TypeError):
            raised.exception.diagnostics[0]["code"] = "changed"
        with self.assertRaises(AttributeError):
            raised.exception.diagnostics = ()
        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        automation["TOPSECRET"] = "TOPSECRET"
        with self.assertRaises(TraceBuildError) as secret:
            build_trace(document, automation, None)
        self.assertNotIn("TOPSECRET", str(secret.exception))
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "build_trace_document.py")], capture_output=True, text=True, check=False)
        self.assertEqual(2, result.returncode)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_canonical_invalidity_precedes_automation_version_classification(self) -> None:
        from tools.build_trace_document import TraceBuildError, build_trace

        document = canonical_document()
        document["test_cases"][0]["steps"][0]["display_order"] = 2
        with self.assertRaises(TraceBuildError) as raised:
            build_trace(document, {"schema_version": "2.1.0", "TOPSECRET": "TOPSECRET"}, None)
        codes = {row["code"] for row in raised.exception.diagnostics}
        self.assertNotIn("V2_1_BREAKING_CHANGE", codes)
        self.assertIn("CANONICAL_DISPLAY_ORDER", codes)
        self.assertNotIn("TOPSECRET", str(raised.exception))

    def test_build_cli_argument_errors_are_one_safe_json_object(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_trace_document.py"), "--unknown", "TOPSECRET"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        self.assertNotIn("TOPSECRET", result.stdout)
        self.assertEqual("error", json.loads(result.stdout)["status"])

    def test_output_is_create_only(self) -> None:
        from tools.build_trace_document import _write_new, build_trace

        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "trace.json"
            target.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                _write_new(target, build_trace(document, automation, run_result(document, automation)))
            self.assertEqual("original", target.read_text(encoding="utf-8"))

    def test_build_cli_v3_and_existing_output_exit_codes(self) -> None:
        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"] = source_for(document)
        run = run_result(document, automation)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            document_path, automation_path, run_path, output = (directory / name for name in ("document.json", "automation.json", "run.json", "trace.json"))
            for path, value in ((document_path, document), (automation_path, automation), (run_path, run)):
                path.write_text(json.dumps(value), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools" / "build_trace_document.py"), "--canonical-document", str(document_path), "--automation-artifact", str(automation_path), "--run-result", str(run_path), "--output", str(output)]
            self.assertEqual(0, subprocess.run(command, capture_output=True, text=True, check=False).returncode)
            self.assertEqual(2, subprocess.run(command, capture_output=True, text=True, check=False).returncode)


if __name__ == "__main__":
    unittest.main()
