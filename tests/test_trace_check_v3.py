from __future__ import annotations

import unittest
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.test_build_trace_v3 import ROOT, generated, run_result
from tests.fixture_factory import canonical_document


class TraceCheckV3Tests(unittest.TestCase):
    def test_trace_only_check_audits_exact_atomic_trace(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document = canonical_document()
        automation = generated(document)
        trace = build_trace(document, automation, run_result(document, automation))
        report = check(trace, require_execution=True)
        self.assertTrue(report["valid"])
        self.assertEqual("PASS", report["trace_audit"]["verdict"])
        self.assertEqual([{"file_id": "FILE-item", "symbol_id": "SYMBOL-item"}], report["trace_audit"]["required_symbol_pairs"])

    def test_exact_cross_artifact_trace_validation_detects_every_registry_mutation(self) -> None:
        from tools.build_trace_document import build_trace, validate_trace_document

        document = canonical_document()
        automation = generated(document)
        run = run_result(document, automation)
        trace = build_trace(document, automation, run)
        families = {
            "requirements": "requirements", "test_cases": "test_cases", "steps": "steps",
            "expectations": "expectations", "assertions": "assertions", "files": "files",
            "symbols": "symbols", "relations": "implementation_relations", "evidence": "execution",
        }
        for name, key in families.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(trace)
                if key == "execution":
                    candidate[key]["evidence"][0]["status"] = "FAILED"
                else:
                    candidate[key][0][next(iter(candidate[key][0]))] = "MUTATED"
                self.assertNotEqual([], validate_trace_document(candidate, document, automation, run))
        candidate = copy.deepcopy(trace)
        candidate["final_verdict"] = "FAIL"
        self.assertNotEqual([], validate_trace_document(candidate, document, automation, run))

    def test_trace_only_rejects_internal_join_breakage_and_partial_external_inputs(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document = canonical_document()
        automation = generated(document)
        run = run_result(document, automation)
        trace = build_trace(document, automation, run)
        broken = copy.deepcopy(trace)
        broken["implementation_relations"][0]["step_id"] = "STEP-foreign"
        self.assertFalse(check(broken)["valid"])
        partial = check(trace, document=document)
        self.assertFalse(partial["valid"])
        self.assertIn("TRACE_EXTERNAL_SET", {row["code"] for row in partial["errors"]})

    def test_trace_only_internal_registry_and_branch_mutation_table(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document, automation = canonical_document(), generated(canonical_document())
        trace = build_trace(document, automation, run_result(document, automation))
        mutations = (
            ("case-requirement", lambda value: value["test_cases"][0]["requirement_ids"].__setitem__(0, "REQ-foreign")),
            ("step-case", lambda value: value["steps"][0].__setitem__("case_id", "TC-foreign")),
            ("expectation-step", lambda value: value["expectations"][0].__setitem__("step_id", "STEP-foreign")),
            ("assertion-parent", lambda value: value["assertions"][0].__setitem__("expectation_id", "EXP-foreign")),
            ("symbol-file", lambda value: value["symbols"][0].__setitem__("file_id", "FILE-foreign")),
            ("duplicate-relation", lambda value: value["implementation_relations"].append(copy.deepcopy(value["implementation_relations"][0]))),
            ("manual-coverage", lambda value: value["steps"][0].__setitem__("manual_only", True)),
            ("blocked-branch", lambda value: value.__setitem__("automation_status", "BLOCKED")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                candidate = copy.deepcopy(trace); mutate(candidate)
                self.assertFalse(check(candidate, require_execution=True)["valid"])

    def test_trace_only_requires_complete_ready_assertion_topology_and_global_parent_order(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document = canonical_document()
        second_case = copy.deepcopy(document["test_cases"][0])
        second_case.update({"case_id": "TC-semantic-fixture-2", "display_order": 2})
        second_step = second_case["steps"][0]
        second_step.update({"step_id": "STEP-2", "display_order": 1})
        second_step["inputs"][0]["input_id"] = "INPUT-2"
        second_step["expectations"][0]["expectation_id"] = "EXP-2"
        second_step["expectations"][0]["assertions"][0]["assertion_id"] = "ASSERT-2"
        document["test_cases"].append(second_case)
        automation = generated(document)
        automation["artifacts"]["implementation_relations"].extend([
            {"kind": "operation", "case_id": "TC-semantic-fixture-2", "step_id": "STEP-2", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
            {"kind": "assertion", "case_id": "TC-semantic-fixture-2", "step_id": "STEP-2", "expectation_id": "EXP-2", "assertion_id": "ASSERT-2", "file_id": "FILE-item", "symbol_id": "SYMBOL-item"},
        ])
        trace = build_trace(document, automation, run_result(document, automation))

        missing = copy.deepcopy(trace)
        missing["assertions"] = []
        missing["implementation_relations"] = [row for row in missing["implementation_relations"] if row["kind"] != "assertion"]
        self.assertFalse(check(missing)["valid"])
        self.assertIn("TRACE_ASSERTION_TOPOLOGY", {row["code"] for row in check(missing)["errors"]})

        reordered = copy.deepcopy(trace)
        for registry in ("steps", "expectations", "assertions"):
            reordered[registry].reverse()
        report = check(reordered)
        self.assertFalse(report["valid"])
        self.assertIn("TRACE_PARENT_ORDER", {row["code"] for row in report["errors"]})

    def test_trace_only_structural_current_evidence_requires_fail_verdict(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document = canonical_document()
        automation = generated(document)
        trace = build_trace(document, automation, run_result(document, automation))
        trace["execution"]["evidence"][0]["symbol_id"] = "SYMBOL-foreign"
        trace["execution"].update({"verdict": "NOT_RUNNABLE", "evidence_authoritative": False})
        trace["final_verdict"] = "NOT_RUNNABLE"
        report = check(trace)
        self.assertFalse(report["valid"])
        self.assertIn("TRACE_EXECUTION_VERDICT", {row["code"] for row in report["errors"]})
        self.assertIn("TRACE_FINAL_VERDICT", {row["code"] for row in report["errors"]})

    def test_stale_evidence_is_ignored_and_missing_current_pair_is_not_runnable(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document, automation = canonical_document(), generated(canonical_document())
        automation["artifacts"]["source"]["source_digest"] = document.get("source_digest", automation["artifacts"]["source"]["source_digest"])
        run = run_result(document, automation)
        run["execution_evidence"][0]["run_id"] = "RUN-old"
        run.update({"verdict": "NOT_RUNNABLE", "evidence_authoritative": False})
        trace = build_trace(document, automation, run)
        self.assertEqual("NOT_RUNNABLE", trace["final_verdict"])
        self.assertTrue(check(trace, require_execution=True)["valid"])

    def test_checker_schema_closure_and_secret_redaction_and_fixture_gate(self) -> None:
        from tools.build_trace_document import build_trace
        from tools.trace_check import check

        document = canonical_document()
        automation = generated(document)
        run = run_result(document, automation)
        trace = build_trace(document, automation, run)
        trace["TOPSECRET"] = "TOPSECRET"
        report = check(trace)
        self.assertFalse(report["valid"])
        self.assertNotIn("TOPSECRET", json.dumps(report))
        fixture = ROOT / "skills" / "orchestrate" / "assets" / "orchestration-fixtures" / "accepted-trace-document.json"
        self.assertTrue(fixture.is_file())
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "trace_check.py"), str(fixture), "--require-execution"], capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode)
        self.assertTrue(json.loads(result.stdout)["valid"])

    def test_checker_exit_codes_distinguish_semantic_and_schema_invalidity(self) -> None:
        from tools.build_trace_document import build_trace

        document, automation = canonical_document(), generated(canonical_document())
        trace = build_trace(document, automation, run_result(document, automation))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.json"
            semantic = copy.deepcopy(trace); semantic["final_verdict"] = "FAIL"
            path.write_text(json.dumps(semantic), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools" / "trace_check.py"), str(path)]
            self.assertEqual(1, subprocess.run(command, capture_output=True, text=True, check=False).returncode)
            schema = copy.deepcopy(trace); schema["unexpected"] = True
            path.write_text(json.dumps(schema), encoding="utf-8")
            self.assertEqual(2, subprocess.run(command, capture_output=True, text=True, check=False).returncode)

    def test_checker_cli_argument_errors_are_one_safe_json_object(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "trace_check.py"), "--unknown", "TOPSECRET"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        self.assertNotIn("TOPSECRET", result.stdout)
        self.assertEqual("error", json.loads(result.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
