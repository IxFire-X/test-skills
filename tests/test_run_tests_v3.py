from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixture_factory import canonical_document  # noqa: E402
from tools.canonical_document import document_sha256  # noqa: E402
from tools.automation_validation import validate_automation_artifact  # noqa: E402
from tools.schema_validation import load_json_strict, schema_diagnostics  # noqa: E402


def digest(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def source_for(document: dict) -> dict:
    return {
        "document_id": document["document_id"],
        "revision": document["revision"],
        "source_digest": document_sha256(document),
    }


def artifact_for(document: dict, files: list[dict], symbols: list[dict]) -> dict:
    """Build a semantically complete generated envelope for the supplied pairs."""
    relations: list[dict] = []
    for symbol in symbols:
        relations.append({
            "kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-1",
            "file_id": symbol["file_id"], "symbol_id": symbol["symbol_id"],
        })
    for symbol in symbols:
        relations.append({
            "kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-1",
            "expectation_id": "EXP-1", "assertion_id": "ASSERT-1",
            "file_id": symbol["file_id"], "symbol_id": symbol["symbol_id"],
        })
    return {
        "schema_version": "3.0.0", "stage": "tc-to-autotest", "warnings": [],
        "artifacts": {
            "automation_status": "GENERATED", "source": source_for(document),
            "generated_files": files, "generated_symbols": symbols,
            "implementation_relations": relations, "manual_dispositions": [], "diagnostics": [],
        },
    }


class FixedResolver:
    def __init__(self, values: dict[tuple[str, str], object]) -> None:
        self.values = values

    def resolve(self, kind: str, name: str) -> object:
        if (kind, name) not in self.values:
            raise LookupError()
        return self.values[(kind, name)]


class ReadyRegistry:
    def require(self, adapter: str, action: str) -> None:
        return None


class RunTestsV3Tests(unittest.TestCase):
    def test_v3_schema_requires_exact_closed_source_and_pair_evidence(self) -> None:
        """A legacy method-id-only report must not validate as V3 execution evidence."""
        schema = load_json_strict(ROOT / "schemas" / "run-tests-output.schema.json")
        legacy = {
            "verdict": "PASS",
            "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": None},
            "environment": {"status": "ready", "interpreter": "Python", "interpreter_path": "python", "working_dir": ".", "missing": None},
            "stats": {"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0},
            "failed_methods": None, "root_cause": None, "raw_output_excerpt": None,
            "ran_at": "2026-08-12T00:00:00+00:00", "exit_code": 0, "run_id": "RUN-current",
            "execution_evidence": [{"run_id": "RUN-current", "method_id": "METHOD-old", "status": "passed"}],
            "evidence_authoritative": True,
        }
        self.assertNotEqual([], schema_diagnostics(legacy, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))
        valid = {
            "schema_version": "3.0.0", "stage": "run-tests", "source": {"document_id": "TCDOC-x", "revision": 1, "source_digest": "sha256:" + "a" * 64}, "verdict": "NOT_RUNNABLE",
            "target": {"language": "python", "framework": "pytest", "runner": "not_applicable", "command": None}, "environment": {"status": "partial", "interpreter": None, "interpreter_path": None, "working_dir": ".", "missing": ["x"]}, "stats": None, "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": "2026-08-12T00:00:00+00:00", "exit_code": None, "run_id": None, "execution_evidence": [], "evidence_authoritative": False, "diagnostics": [{"path": "/x", "code": "X", "message": "x"}],
        }
        self.assertEqual([], schema_diagnostics(valid, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))
        valid["extra"] = True
        self.assertNotEqual([], schema_diagnostics(valid, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))
        del valid["extra"]
        valid.update({"verdict": "PASS", "run_id": None, "execution_evidence": [], "evidence_authoritative": False, "stats": None, "exit_code": None, "diagnostics": []})
        self.assertNotEqual([], schema_diagnostics(valid, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))

    def test_v3_loader_and_compatibility_preserve_file_symbol_identity(self) -> None:
        """Collapsing equal local symbol IDs across files would lose a required execution."""
        from tools.run_tests import load_automation_artifact, validate_artifact_runner_compatibility

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            first = b"def test_first():\n    assert True\n"
            second = b"def test_second():\n    assert True\n"
            (project / "tests" / "test_first.py").write_bytes(first)
            (project / "tests" / "test_second.py").write_bytes(second)
            artifact = artifact_for(document, [
                {"file_id": "FILE-first", "path": "tests/test_first.py", "language": "python", "framework": "pytest", "content_digest": digest(first)},
                {"file_id": "FILE-second", "path": "tests/test_second.py", "language": "python", "framework": "pytest", "content_digest": digest(second)},
            ], [
                {"file_id": "FILE-first", "symbol_id": "SYMBOL-shared", "locator": {"kind": "python_module_function", "function_name": "test_first"}},
                {"file_id": "FILE-second", "symbol_id": "SYMBOL-shared", "locator": {"kind": "python_module_function", "function_name": "test_second"}},
            ])
            artifact_path = project / "automation.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            loaded = load_automation_artifact(artifact_path)
            compatibility = validate_artifact_runner_compatibility(project, "python", document, loaded)
            self.assertEqual("READY", compatibility.status)
            self.assertEqual((("FILE-first", "SYMBOL-shared"), ("FILE-second", "SYMBOL-shared")), compatibility.required_pairs)
            self.assertEqual({"FILE-first": digest(first), "FILE-second": digest(second)}, dict(compatibility.verified_file_digests))
            with self.assertRaises(TypeError):
                compatibility.verified_file_digests["FILE-other"] = digest(b"x")  # type: ignore[index]
            with self.assertRaises(TypeError):
                compatibility.bindings[("FILE-first", "SYMBOL-shared")]["locator"]["kind"] = "changed"  # type: ignore[index]

    def test_loader_rejects_v21_with_the_exact_breaking_code(self) -> None:
        """Falling back to a legacy loader would silently reintroduce global method identity."""
        from tools.run_tests import RunnerInputError, load_automation_artifact

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.json"
            path.write_text('{"schema_version":"2.1.0"}', encoding="utf-8")
            with self.assertRaisesRegex(RunnerInputError, "V2_1_BREAKING_CHANGE"):
                load_automation_artifact(path)

    def test_evidence_semantics_are_current_pair_scoped_and_truthful(self) -> None:
        """A stale, duplicate, foreign, or missing pair must never become a PASS."""
        from tools.run_tests import validate_execution_evidence

        source = {"document_id": "TCDOC-semantic-fixture", "revision": 1, "source_digest": "sha256:" + "a" * 64}
        digests = {"FILE-a": "sha256:" + "b" * 64, "FILE-b": "sha256:" + "c" * 64}
        required = (("FILE-a", "SYMBOL-same"), ("FILE-b", "SYMBOL-same"))

        def row(file_id: str, status: str, **changes: str) -> dict:
            value = {"run_id": "RUN-current", "source_digest": source["source_digest"], "file_id": file_id,
                     "symbol_id": "SYMBOL-same", "file_digest": digests[file_id], "status": status}
            value.update(changes)
            return value

        cases = (
            ("passed", "PASS", True, [row("FILE-a", "PASSED"), row("FILE-b", "PASSED")], None),
            ("failed", "FAIL", True, [row("FILE-a", "FAILED"), row("FILE-b", "PASSED")], None),
            ("error", "FAIL", True, [row("FILE-a", "ERROR"), row("FILE-b", "PASSED")], None),
            ("skipped", "NOT_RUNNABLE", True, [row("FILE-a", "SKIPPED"), row("FILE-b", "PASSED")], None),
            ("missing", "NOT_RUNNABLE", False, [row("FILE-a", "PASSED")], None),
            ("stale", "NOT_RUNNABLE", False, [row("FILE-a", "PASSED", run_id="RUN-old"), row("FILE-b", "PASSED")], None),
            ("duplicate", "FAIL", False, [row("FILE-a", "PASSED"), row("FILE-a", "PASSED"), row("FILE-b", "PASSED")], "RUNNER_DUPLICATE_EVIDENCE"),
            ("conflict", "FAIL", False, [row("FILE-a", "PASSED"), row("FILE-a", "FAILED"), row("FILE-b", "PASSED")], "RUNNER_DUPLICATE_EVIDENCE"),
            ("wrong-digest", "FAIL", False, [row("FILE-a", "PASSED", file_digest="sha256:" + "d" * 64), row("FILE-b", "PASSED")], "RUNNER_EVIDENCE_FILE_DIGEST"),
            ("wrong-source", "FAIL", False, [row("FILE-a", "PASSED", source_digest="sha256:" + "e" * 64), row("FILE-b", "PASSED")], "RUNNER_EVIDENCE_SOURCE"),
            ("foreign", "FAIL", False, [row("FILE-a", "PASSED"), {**row("FILE-b", "PASSED"), "file_id": "FILE-foreign"}], "RUNNER_FOREIGN_EVIDENCE"),
        )
        for name, verdict, authoritative, evidence, expected_code in cases:
            with self.subTest(name=name):
                diagnostics = validate_execution_evidence(verdict, "RUN-current", source, evidence, authoritative, required, digests)
                if expected_code is None:
                    self.assertEqual([], diagnostics)
                else:
                    self.assertIn(expected_code, {row["code"] for row in diagnostics})

    def test_junit_records_are_file_scoped_and_missing_pairs_stay_missing(self) -> None:
        """One same-named node must not satisfy a second generated file or synthesize an observation."""
        from tools.run_tests import _compat, _records

        source = {"source_digest": "sha256:" + "a" * 64}
        bindings = {
            ("FILE-a", "SYMBOL-same"): {"path": Path("tests/a.py"), "locator": {"kind": "python_module_function", "function_name": "test_same"}, "node": "test_same"},
            ("FILE-b", "SYMBOL-same"): {"path": Path("tests/b.py"), "locator": {"kind": "python_module_function", "function_name": "test_same"}, "node": "test_same"},
        }
        compatibility = _compat("READY", bindings=bindings, pairs=tuple(bindings), digests={"FILE-a": "sha256:" + "b" * 64, "FILE-b": "sha256:" + "c" * 64})
        with tempfile.TemporaryDirectory() as temporary:
            xml = Path(temporary) / "result.xml"
            xml.write_text('<testsuite><testcase file="tests/a.py" classname="tests.a" name="test_same"/></testsuite>', encoding="utf-8")
            rows = _records(xml, compatibility, "RUN-current", source)
        self.assertEqual([("FILE-a", "PASSED")], [(row["file_id"], row["status"]) for row in rows])

    def test_java_locator_and_records_reject_other_class_and_stale_xml(self) -> None:
        """A method in another Java class or a prior report cannot prove this target."""
        from tools.run_tests import _compat, _java_node, _records

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            java = root / "SampleTest.java"
            java.write_text("package example; class SampleTest {} class Helper { public void testRun() {} }", encoding="utf-8")
            self.assertIsNone(_java_node(java, {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "testRun"}))
            stale = root / "TEST-stale.xml"
            stale.write_text('<testsuite><testcase classname="example.SampleTest" name="testRun"/></testsuite>', encoding="utf-8")
            compatibility = _compat("READY", pairs=(("FILE-java", "SYMBOL-java"),), bindings={("FILE-java", "SYMBOL-java"): {"path": java, "locator": {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "testRun"}, "node": "testRun"}}, digests={"FILE-java": "sha256:" + "b" * 64})
            self.assertEqual([], _records(root, compatibility, "RUN-current", {"source_digest": "sha256:" + "a" * 64}, True, set()))

    def test_java_locator_requires_a_direct_outer_class_member(self) -> None:
        """A nested declaration must not satisfy the outer class's generated locator."""
        from tools.run_tests import _java_node

        locator = {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "testRun"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SampleTest.java"
            cases = (
                ("nested only", "package example; class SampleTest { class Nested { void testRun() {} } }", None),
                ("direct", "package example; class SampleTest { void testRun() {} class Nested { void testRun() {} } }", "testRun"),
                ("overload", "package example; class SampleTest { void testRun() {} void testRun(int x) {} }", None),
                ("comments and strings", 'package example; class SampleTest { String marker = "class Nested { void testRun() {} }"; /* class Nested { void testRun() {} } */ void testRun() {} }', "testRun"),
            )
            for name, source, expected in cases:
                with self.subTest(name=name):
                    path.write_text(source, encoding="utf-8")
                    self.assertEqual(expected, _java_node(path, locator))

    def test_falsey_host_objects_are_passed_to_preflight(self) -> None:
        """Using `or` would discard an explicitly supplied falsey host integration."""
        from tools.run_tests import run_tests_v3

        class FalseyResolver(FixedResolver):
            def __bool__(self) -> bool: return False
        class FalseyRegistry(ReadyRegistry):
            def __bool__(self) -> bool: return False
        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"; (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            report = run_tests_v3(project, "python", document, artifact, FalseyResolver({("environment", "API_BASE_URL"): "https://api.example"}), FalseyRegistry())
        self.assertNotEqual("NOT_RUNNABLE", report["verdict"])

    def test_direct_cli_valid_input_is_compact_json_without_traceback(self) -> None:
        """Direct-script imports must work outside the repository module invocation path."""
        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"; (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            document_path, artifact_path = project / "document.json", project / "artifact.json"
            document_path.write_text(json.dumps(document), encoding="utf-8"); artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "run_tests.py"), "--project", str(project), "--language", "python", "--canonical-document", str(document_path), "--automation-artifact", str(artifact_path)], capture_output=True, text=True, check=False)
        self.assertIn(result.returncode, {0, 1, 2})
        self.assertNotIn("Traceback", result.stderr + result.stdout)
        self.assertIsInstance(json.loads(result.stdout), dict)

    def test_runner_dispatches_proven_python_locators_only_without_provider_injection(self) -> None:
        """Running whole files or injecting resolved values could execute unrelated code or leak a provider."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = (
                "def test_selected():\n    assert True\n\n"
                "class TestOne:\n    def test_same(self):\n        assert True\n\n"
                "class TestTwo:\n    def test_same(self):\n        assert False\n\n"
                "def test_unrelated():\n    assert False\n"
            ).encode()
            (project / "tests" / "test_selected.py").write_bytes(content)
            symbols = [
                {"file_id": "FILE-python", "symbol_id": "SYMBOL-module", "locator": {"kind": "python_module_function", "function_name": "test_selected"}},
                {"file_id": "FILE-python", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_class_method", "qualified_class_name": "TestOne", "method_name": "test_same"}},
                {"file_id": "FILE-python", "symbol_id": "SYMBOL-two", "locator": {"kind": "python_class_method", "qualified_class_name": "TestTwo", "method_name": "test_same"}},
            ]
            artifact = artifact_for(document, [{"file_id": "FILE-python", "path": "tests/test_selected.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], symbols)
            report = run_tests_v3(project, "python", document, artifact, FixedResolver({("environment", "API_BASE_URL"): "https://api.example"}), ReadyRegistry())
            self.assertEqual("FAIL", report["verdict"])
            self.assertIsNotNone(report["run_id"])
            self.assertIsNone(report["raw_output_excerpt"])
            self.assertEqual([("FILE-python", "SYMBOL-module"), ("FILE-python", "SYMBOL-one"), ("FILE-python", "SYMBOL-two")], [(row["file_id"], row["symbol_id"]) for row in report["execution_evidence"]])
            self.assertEqual(["PASSED", "PASSED", "FAILED"], [row["status"] for row in report["execution_evidence"]])
            self.assertEqual([], schema_diagnostics(report, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))

    def test_python_runner_sets_selected_project_as_pytest_rootdir(self) -> None:
        """JUnit classnames must stay relative to the selected module root."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"
            (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(
                document,
                [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}],
                [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}],
            )
            resolver = FixedResolver({("environment", "API_BASE_URL"): "https://api.example"})
            with mock.patch("tools.run_tests._python", return_value=sys.executable), mock.patch(
                "tools.run_tests.run_subprocess", side_effect=[(0, "", ""), (0, "", "")]
            ) as runner:
                run_tests_v3(project, "python", document, artifact, resolver, ReadyRegistry())

        command = runner.call_args_list[1].args[0]
        self.assertEqual(
            [sys.executable, "-m", "pytest", "-q", "--rootdir", str(project.resolve())],
            command[:6],
        )
        self.assertEqual("--junitxml", command[6])

    def test_blocked_and_manual_artifacts_reject_direct_execution_without_a_report(self) -> None:
        """Treating blocked/manual work as NOT_RUNNABLE evidence would fabricate an execution attempt."""
        from tools.run_tests import RunnerInputError, run_tests_v3

        document = canonical_document()
        step = document["test_cases"][0]["steps"][0]
        step.update({"manual_only": True, "manual_reason": "Physical check.", "operation": None, "inputs": [], "outputs": []})
        step["expectations"][0]["assertions"] = []
        artifact = {"schema_version": "3.0.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
            "automation_status": "GENERATED", "source": source_for(document), "generated_files": [], "generated_symbols": [],
            "implementation_relations": [], "manual_dispositions": [{"case_id": "TC-semantic-fixture", "step_id": "STEP-1"}], "diagnostics": []}}
        self.assertEqual([], validate_automation_artifact(artifact, document))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RunnerInputError, "RUNNER_NO_AUTOMATABLE_SYMBOLS"):
                run_tests_v3(Path(temporary), "python", document, artifact)

        blocked = canonical_document()
        blocked_step = blocked["test_cases"][0]["steps"][0]
        blocked_step.update({"operation": None, "inputs": [], "outputs": [], "automation_blockers": [{"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Operation unavailable.", "provenance": ["https://example.invalid/context"]}]})
        blocked_artifact = {"schema_version": "3.0.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
            "automation_status": "BLOCKED", "source": source_for(blocked), "generated_files": [], "generated_symbols": [],
            "implementation_relations": [], "manual_dispositions": [], "diagnostics": [{"path": "/test_cases/0/steps/0/operation", "code": "UNRESOLVED_OPERATION", "message": "Operation unavailable."}]}}
        self.assertEqual([], validate_automation_artifact(blocked_artifact, blocked))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RunnerInputError, "RUNNER_AUTOMATION_BLOCKED"):
                run_tests_v3(Path(temporary), "python", blocked, blocked_artifact)

    def test_compatibility_blocks_bad_bytes_paths_languages_and_locators_before_runtime(self) -> None:
        """A bypass of any static proof could execute changed, escaped, or ambiguous source."""
        from tools.run_tests import validate_artifact_runner_compatibility

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"
            file = project / "tests" / "test_one.py"
            file.write_bytes(content)
            base = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            cases = (
                ("modified", lambda value: value["artifacts"]["generated_files"][0].__setitem__("content_digest", digest(b"other")), "RUNNER_FILE_DIGEST"),
                ("wrong-framework", lambda value: value["artifacts"]["generated_files"][0].__setitem__("framework", "junit5"), "RUNNER_LANGUAGE_FRAMEWORK"),
                ("missing-locator", lambda value: value["artifacts"]["generated_symbols"][0]["locator"].__setitem__("function_name", "test_missing"), "RUNNER_LOCATOR"),
            )
            for name, mutate, code in cases:
                with self.subTest(name=name):
                    candidate = copy.deepcopy(base)
                    mutate(candidate)
                    result = validate_artifact_runner_compatibility(project, "python", document, candidate)
                    self.assertEqual("NOT_RUNNABLE", result.status)
                    self.assertIn(code, {row["code"] for row in result.diagnostics})
            outside = project.parent / "outside.py"
            outside.write_bytes(content)
            escaped = copy.deepcopy(base)
            escaped["artifacts"]["generated_files"][0]["path"] = "../outside.py"
            result = validate_artifact_runner_compatibility(project, "python", document, escaped)
            self.assertEqual("NOT_RUNNABLE", result.status)
            link = project / "tests" / "escaped.py"
            try:
                os.symlink(outside, link)
            except OSError:
                self.skipTest("symlink creation is unavailable on this workstation")
            symlinked = copy.deepcopy(base)
            symlinked["artifacts"]["generated_files"][0].update({"path": "tests/escaped.py", "content_digest": digest(content)})
            self.assertEqual("NOT_RUNNABLE", validate_artifact_runner_compatibility(project, "python", document, symlinked).status)

    def test_static_python_and_java_locator_matrix_is_conservative(self) -> None:
        """Wrong nested class or overloaded Java method must stop before a target process begins."""
        from tools.run_tests import validate_artifact_runner_compatibility

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            py = b"class Outer:\n    class Inner:\n        def test_same(self):\n            assert True\n\nclass Other:\n    def test_same(self):\n        assert True\n"
            java = b"package example;\npublic class SampleTest { public void testRun() {} }\n"
            (project / "tests" / "test_nested.py").write_bytes(py)
            (project / "tests" / "SampleTest.java").write_bytes(java)
            python_artifact = artifact_for(document, [{"file_id": "FILE-py", "path": "tests/test_nested.py", "language": "python", "framework": "pytest", "content_digest": digest(py)}], [{"file_id": "FILE-py", "symbol_id": "SYMBOL-nested", "locator": {"kind": "python_class_method", "qualified_class_name": "Outer.Inner", "method_name": "test_same"}}])
            self.assertEqual("READY", validate_artifact_runner_compatibility(project, "python", document, python_artifact).status)
            bad_python = copy.deepcopy(python_artifact)
            bad_python["artifacts"]["generated_symbols"][0]["locator"]["qualified_class_name"] = "Outer.Other"
            self.assertEqual("NOT_RUNNABLE", validate_artifact_runner_compatibility(project, "python", document, bad_python).status)
            java_artifact = artifact_for(document, [{"file_id": "FILE-java", "path": "tests/SampleTest.java", "language": "java", "framework": "junit5", "content_digest": digest(java)}], [{"file_id": "FILE-java", "symbol_id": "SYMBOL-java", "locator": {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "testRun"}}])
            self.assertEqual("READY", validate_artifact_runner_compatibility(project, "java", document, java_artifact).status)
            java = b"package example;\npublic class SampleTest { public void testRun() {} public void testRun(int x) {} }\n"
            (project / "tests" / "SampleTest.java").write_bytes(java)
            java_artifact["artifacts"]["generated_files"][0]["content_digest"] = digest(java)
            self.assertEqual("NOT_RUNNABLE", validate_artifact_runner_compatibility(project, "java", document, java_artifact).status)

    def test_each_target_attempt_has_a_fresh_run_id(self) -> None:
        """Deriving IDs from source content would make stale evidence look current."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"
            (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            resolver = FixedResolver({("environment", "API_BASE_URL"): "https://api.example"})
            first = run_tests_v3(project, "python", document, artifact, resolver, ReadyRegistry())
            second = run_tests_v3(project, "python", document, artifact, resolver, ReadyRegistry())
            self.assertEqual(("PASS", "PASS"), (first["verdict"], second["verdict"]))
            self.assertNotEqual(first["run_id"], second["run_id"])

    def test_failing_target_output_cannot_reach_v3_report(self) -> None:
        """Copying pytest output into a report could disclose provider values or test secrets."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = b"def test_secret():\n    assert False, 'TOPSECRET-RUNNER-OUTPUT'\n"
            (project / "tests" / "test_secret.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-secret", "path": "tests/test_secret.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-secret", "symbol_id": "SYMBOL-secret", "locator": {"kind": "python_module_function", "function_name": "test_secret"}}])
            report = run_tests_v3(project, "python", document, artifact, FixedResolver({("environment", "API_BASE_URL"): "https://api.example"}), ReadyRegistry())
            self.assertEqual("FAIL", report["verdict"])
            self.assertNotIn("TOPSECRET-RUNNER-OUTPUT", json.dumps(report))
            self.assertEqual((None, None, None), (report["raw_output_excerpt"], report["root_cause"], report["failed_methods"]))

    def test_runner_input_error_is_immutable_and_safe(self) -> None:
        """Mutable direct-invocation diagnostics could be changed into a misleading runnable branch."""
        from tools.run_tests import RunnerInputError

        error = RunnerInputError("RUNNER_AUTOMATION_BLOCKED")
        self.assertEqual("RUNNER_AUTOMATION_BLOCKED", error.code)
        with self.assertRaises(TypeError):
            error.diagnostics[0]["code"] = "other"  # type: ignore[index]
        with self.assertRaises(Exception):
            error.code = "other"  # type: ignore[misc]
        with self.assertRaises(Exception):
            error.diagnostics = ()  # type: ignore[misc]

    def test_preflight_failure_starts_no_target_process_or_evidence(self) -> None:
        """Running a target before global provider readiness could leak or partially execute tests."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"
            (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            with mock.patch("tools.run_tests.run_subprocess") as target, mock.patch("tools.run_tests.subprocess.run") as process:
                report = run_tests_v3(project, "python", document, artifact)
            self.assertEqual((0, 0), (target.call_count, process.call_count))
            self.assertEqual(("NOT_RUNNABLE", None, [], False), (report["verdict"], report["run_id"], report["execution_evidence"], report["evidence_authoritative"]))
            self.assertIn("PREFLIGHT_PROVIDER_MISSING", {row["code"] for row in report["diagnostics"]})
            self.assertEqual([], schema_diagnostics(report, ROOT / "schemas" / "run-tests-output.schema.json", ROOT))

    def test_source_mismatch_stops_before_every_subprocess_boundary(self) -> None:
        """A generated file for different canonical bytes must not reach readiness probes."""
        from tools.run_tests import run_tests_v3

        document = canonical_document()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); (project / "tests").mkdir()
            content = b"def test_one():\n    assert True\n"; (project / "tests" / "test_one.py").write_bytes(content)
            artifact = artifact_for(document, [{"file_id": "FILE-one", "path": "tests/test_one.py", "language": "python", "framework": "pytest", "content_digest": digest(content)}], [{"file_id": "FILE-one", "symbol_id": "SYMBOL-one", "locator": {"kind": "python_module_function", "function_name": "test_one"}}])
            artifact["artifacts"]["source"]["source_digest"] = "sha256:" + "0" * 64
            with mock.patch("tools.run_tests.run_subprocess") as target, mock.patch("tools.run_tests.subprocess.run") as process:
                report = run_tests_v3(project, "python", document, artifact)
            self.assertEqual(("NOT_RUNNABLE", 0, 0), (report["verdict"], target.call_count, process.call_count))

    def test_cli_help_and_required_v3_arguments(self) -> None:
        """The public CLI must not retain the legacy optional artifact route."""
        help_result = subprocess.run([sys.executable, str(ROOT / "tools" / "run_tests.py"), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(0, help_result.returncode)
        self.assertIn("--canonical-document", help_result.stdout)
        self.assertIn("--automation-artifact", help_result.stdout)
        missing = subprocess.run([sys.executable, str(ROOT / "tools" / "run_tests.py")], capture_output=True, text=True, check=False)
        self.assertEqual(2, missing.returncode)


if __name__ == "__main__":
    unittest.main()
