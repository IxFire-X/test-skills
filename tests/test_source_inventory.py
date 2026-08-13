import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.discover_project import discover_project
from tools.init_skillsrc import compile_skillsrc
from tools.test_classification import (
    SuppliedInput,
    TestClassificationError,
    build_source_inventories,
)


def digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class SourceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("tests/test_sample.py", """\
import pytest
\n\ndef test_module():
    pass
\n\nasync def test_async():
    pass
\n\nclass TestSample:
    def test_method(self):
        pass
\n\ndef helper():
    pass
""")
        self.write("src/service.py", "def create_order():\n    return 'created'\n")
        self.write("docs/feature.md", "Orders can be created.\n")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, portable_path, content):
        path = self.root / portable_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return path

    def skillsrc(self, test_roots=("tests",), language="python", source_roots=("src",), feature_sources=("docs",)):
        return {
            "version": "3.0",
            "project": {"name": "sample"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{
                "id": "backend", "root": ".",
                "stack": {"language": language, "build_tool": "pip"},
                "paths": {"tests": list(test_roots), "source": list(source_roots)},
                "feature_sources": {"requirements": list(feature_sources)},
                "detected_from": ["pyproject.toml"],
            }],
        }

    def test_one_file_can_own_multiple_symbols(self):
        result = build_source_inventories(self.root, self.skillsrc(), "backend", ())
        self.assertEqual(1, len(result.technical_test_inventory["files"]))
        self.assertEqual(3, len(result.technical_test_inventory["symbols"]))

    def test_no_test_roots_is_valid_empty_inventory(self):
        value = build_source_inventories(self.root, self.skillsrc(test_roots=()), "backend", ())
        self.assertEqual([], value.technical_test_inventory["files"])
        self.assertEqual([], value.technical_test_inventory["symbols"])

    def test_discovery_manifest_includes_colocated_test_and_retains_sibling_product_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src/pkg").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nname="sample"\n', encoding="utf-8")
            (root / "src/service.py").write_text("def service(): pass\n", encoding="utf-8")
            (root / "src/pkg/test_api.py").write_text("def test_api(): pass\n", encoding="utf-8")
            (root / "src/.secrets").mkdir()
            (root / "src/.secrets/test_secret.py").write_text("def test_secret(): pass\n", encoding="utf-8")

            discovery = discover_project(root)
            self.assertEqual(["src/pkg/test_api.py"], discovery["modules"][0]["paths"]["tests"])
            skillsrc = compile_skillsrc(discovery, {})
            result = build_source_inventories(root, skillsrc, "root", ())

            self.assertEqual(["src/pkg/test_api.py"], [row["path"] for row in result.technical_test_inventory["files"]])
            self.assertEqual([{"kind": "python_module_function", "function_name": "test_api"}], [dict(row["locator"]) for row in result.technical_test_inventory["symbols"]])
            self.assertIn("src/service.py", [row["path"] for row in result.authorized_behavior_sources["sources"]])

    def test_discovery_and_inventory_share_anchored_generated_build_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src/backend/InvenTree/build").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nname="sample"\n', encoding="utf-8")
            (root / "src/backend/InvenTree/build/service.py").write_text("def service(): pass\n", encoding="utf-8")
            (root / "src/backend/InvenTree/build/test_api.py").write_text("def test_api(): pass\n", encoding="utf-8")
            (root / "src/build").mkdir()
            (root / "src/build/generated.py").write_text("generated = True\n", encoding="utf-8")
            (root / "src/build/test_generated.py").write_text("def test_generated(): pass\n", encoding="utf-8")

            discovery = discover_project(root)
            self.assertEqual(["src/backend/InvenTree/build/test_api.py"], discovery["modules"][0]["paths"]["tests"])
            result = build_source_inventories(root, compile_skillsrc(discovery, {}), "root", ())

            self.assertEqual(["src/backend/InvenTree/build/test_api.py"], [row["path"] for row in result.technical_test_inventory["files"]])
            sources = [row["path"] for row in result.authorized_behavior_sources["sources"]]
            self.assertIn("src/backend/InvenTree/build/service.py", sources)
            self.assertNotIn("src/backend/InvenTree/build/test_api.py", sources)
            self.assertNotIn("src/build/generated.py", sources)

    def test_python_module_async_class_and_parametrized_functions_use_closed_locators(self):
        self.write("tests/test_sample.py", """\
import pytest
\n\ndef test_module(): pass
\n\nasync def test_async(): pass
\n\nclass TestSample:
    def test_method(self): pass
\n\n@pytest.mark.parametrize('value', [1, 2])
def test_parameterized(value): pass
\n\ndef helper(): pass
""")
        result = build_source_inventories(self.root, self.skillsrc(), "backend", ())
        locators = [dict(row["locator"]) for row in result.technical_test_inventory["symbols"]]
        self.assertEqual([
            {"kind": "python_module_function", "function_name": "test_async"},
            {"kind": "python_module_function", "function_name": "test_module"},
            {"kind": "python_module_function", "function_name": "test_parameterized"},
            {"kind": "python_class_method", "qualified_class_name": "TestSample", "method_name": "test_method"},
        ], locators)

    def test_unittest_testcase_class_is_supported_and_helpers_are_excluded(self):
        self.write("tests/test_sample.py", """\
import unittest
\n\nclass Checks(unittest.TestCase):
    def test_from_base(self): pass
    def helper(self): pass
\n\nclass Helper:
    def test_not_a_test_class(self): pass
""")
        result = build_source_inventories(self.root, self.skillsrc(), "backend", ())
        self.assertEqual([{"kind": "python_class_method", "qualified_class_name": "Checks", "method_name": "test_from_base"}], [dict(row["locator"]) for row in result.technical_test_inventory["symbols"]])

    def test_java_test_and_parameterized_test_are_supported(self):
        self.write("tests/SampleTest.java", """\
package example;
class SampleTest {
  @Test void first() {}
  @ParameterizedTest void second(String value) {}
  void helper() {}
}
""")
        result = build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
        self.assertEqual([
            {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "first"},
            {"kind": "java_class_method", "class_fqn": "example.SampleTest", "method_name": "second"},
        ], [dict(row["locator"]) for row in result.technical_test_inventory["symbols"]])

    def test_annotated_non_file_stem_java_owner_is_unrepresentable(self):
        self.write("tests/SampleTest.java", """\
package example;
class SampleTest { @Test void included() {} }
class Helper { @Test void excluded() {} }
""")
        with self.assertRaises(TestClassificationError) as raised:
            build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
        self.assertEqual("INVENTORY_UNSUPPORTED_LOCATOR", raised.exception.diagnostics[0]["code"])
        self.assertEqual("tests/SampleTest.java", raised.exception.diagnostics[0]["path"])

    def test_annotated_nested_java_owner_is_unrepresentable(self):
        self.write("tests/SampleTest.java", """\
class SampleTest {
  class Nested { @Test void nested() {} }
}
""")
        with self.assertRaises(TestClassificationError) as raised:
            build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
        self.assertEqual("INVENTORY_UNSUPPORTED_LOCATOR", raised.exception.diagnostics[0]["code"])
        self.assertEqual("tests/SampleTest.java", raised.exception.diagnostics[0]["path"])

    def test_java_test_with_an_additional_annotation_is_not_omitted(self):
        self.write("tests/SampleTest.java", """\
class SampleTest {
  @Test
  @Tag("fast")
  void included() {}
}
""")
        result = build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
        self.assertEqual([{"kind": "java_class_method", "class_fqn": "SampleTest", "method_name": "included"}], [dict(row["locator"]) for row in result.technical_test_inventory["symbols"]])

    def test_unrepresentable_supported_test_is_not_silently_omitted(self):
        self.write("tests/SampleTest.java", """\
class SampleTest {
  @Test void same() {}
  @Test void same(int value) {}
}
""")
        with self.assertRaises(TestClassificationError) as raised:
            build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
        self.assertEqual("INVENTORY_UNSUPPORTED_LOCATOR", raised.exception.diagnostics[0]["code"])

    def test_overlapping_roots_deduplicate_resolved_files(self):
        result = build_source_inventories(self.root, self.skillsrc(test_roots=("tests", "tests/.")), "backend", ())
        self.assertEqual(1, len(result.technical_test_inventory["files"]))

    def test_missing_root_and_unsafe_path_are_errors(self):
        for roots, code in ((("missing",), "INVENTORY_MISSING_ROOT"), (("../outside",), "INVENTORY_UNSAFE_PATH")):
            with self.subTest(roots=roots), self.assertRaises(TestClassificationError) as raised:
                build_source_inventories(self.root, self.skillsrc(test_roots=roots), "backend", ())
            self.assertEqual(code, raised.exception.diagnostics[0]["code"])

    def test_parse_and_decode_failures_are_inventory_errors_with_immutable_diagnostics(self):
        for content, code in (("def test_broken(:\n", "INVENTORY_PARSE_ERROR"),):
            self.write("tests/test_sample.py", content)
            with self.subTest(code=code), self.assertRaises(TestClassificationError) as raised:
                build_source_inventories(self.root, self.skillsrc(), "backend", ())
            self.assertEqual(code, raised.exception.diagnostics[0]["code"])
            with self.assertRaises(TypeError):
                raised.exception.diagnostics[0]["code"] = "changed"
        (self.root / "tests" / "test_sample.py").write_bytes(b"\xff")
        with self.assertRaises(TestClassificationError) as raised:
            build_source_inventories(self.root, self.skillsrc(), "backend", ())
        self.assertEqual("INVENTORY_DECODE_ERROR", raised.exception.diagnostics[0]["code"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_symlink_escape_is_an_error(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        try:
            try:
                os.symlink(outside, self.root / "tests" / "escaped")
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error.winerror}")
            with self.assertRaises(TestClassificationError) as raised:
                build_source_inventories(self.root, self.skillsrc(), "backend", ())
            self.assertEqual("INVENTORY_SYMLINK_ESCAPE", raised.exception.diagnostics[0]["code"])
        finally:
            outside.rmdir()

    def test_duplicate_supplied_ids_are_errors(self):
        supplied = (SuppliedInput("REQ-one", b"one"), SuppliedInput("REQ-one", b"two"))
        with self.assertRaises(TestClassificationError) as raised:
            build_source_inventories(self.root, self.skillsrc(), "backend", supplied)
        self.assertEqual("INVENTORY_DUPLICATE_SOURCE_ID", raised.exception.diagnostics[0]["code"])

    def test_product_filters_exclude_test_secret_and_build_paths_before_hashing(self):
        self.write("src/.env", "secret")
        self.write("src/key.pem", "secret")
        self.write("src/build/generated.py", "generated")
        self.write("src/test_ignored.py", "def test_nope(): pass")
        result = build_source_inventories(self.root, self.skillsrc(), "backend", ())
        self.assertEqual(["docs/feature.md", "src/service.py"], [row["path"] for row in result.authorized_behavior_sources["sources"]])

    def test_declared_test_root_is_excluded_when_it_overlaps_source_roots(self):
        self.write("tests/helper.py", "def fixture_helper(): pass\n")
        self.write("tests/fixtures/example.txt", "test-only fixture\n")
        result = build_source_inventories(self.root, self.skillsrc(source_roots=("src", "tests")), "backend", ())
        self.assertEqual(["docs/feature.md", "src/service.py"], [row["path"] for row in result.authorized_behavior_sources["sources"]])

    def test_exact_ids_digests_and_canonical_order_are_hand_derived(self):
        result = build_source_inventories(self.root, self.skillsrc(), "backend", (SuppliedInput("REQ-synthetic", b"must create an order\n"),))
        inventory = result.technical_test_inventory
        file_id = "FILE-" + hashlib.sha256(b"tests/test_sample.py").hexdigest()
        expected_inventory = {
            "module_id": "backend", "test_roots": ["tests"],
            "files": [{"file_id": file_id, "path": "tests/test_sample.py", "language": "python", "framework": "pytest", "content_digest": "sha256:" + hashlib.sha256((self.root / "tests/test_sample.py").read_bytes()).hexdigest()}],
            "symbols": [
                {"file_id": file_id, "symbol_id": "SYMBOL-" + hashlib.sha256(b'{"function_name":"test_async","kind":"python_module_function"}').hexdigest(), "locator": {"kind": "python_module_function", "function_name": "test_async"}},
                {"file_id": file_id, "symbol_id": "SYMBOL-" + hashlib.sha256(b'{"function_name":"test_module","kind":"python_module_function"}').hexdigest(), "locator": {"kind": "python_module_function", "function_name": "test_module"}},
                {"file_id": file_id, "symbol_id": "SYMBOL-" + hashlib.sha256(b'{"kind":"python_class_method","method_name":"test_method","qualified_class_name":"TestSample"}').hexdigest(), "locator": {"kind": "python_class_method", "qualified_class_name": "TestSample", "method_name": "test_method"}},
            ],
        }
        self.assertEqual(expected_inventory, inventory)
        self.assertEqual(digest(expected_inventory), result.technical_test_inventory_sha256)
        expected_sources = {
            "module_id": "backend",
            "sources": [
                {"source_id": "REQ-synthetic", "kind": "supplied_requirement", "content_digest": "sha256:" + hashlib.sha256(b"must create an order\n").hexdigest()},
                {"source_id": "SOURCE-" + hashlib.sha256(b"product_file\0docs/feature.md").hexdigest(), "kind": "product_file", "path": "docs/feature.md", "content_digest": "sha256:" + hashlib.sha256(b"Orders can be created.\n").hexdigest()},
                {"source_id": "SOURCE-" + hashlib.sha256(b"product_file\0src/service.py").hexdigest(), "kind": "product_file", "path": "src/service.py", "content_digest": "sha256:" + hashlib.sha256(b"def create_order():\n    return 'created'\n").hexdigest()},
            ],
        }
        self.assertEqual(expected_sources, {"module_id": result.authorized_behavior_sources["module_id"], "sources": [dict(row) for row in result.authorized_behavior_sources["sources"]]})
        self.assertEqual(digest(expected_sources), result.authorized_behavior_sources_sha256)
        with self.assertRaises(TypeError):
            result.technical_test_inventory["files"] = ()
        with self.assertRaises(TypeError):
            result.technical_test_inventory["files"][0]["path"] = "changed"


if __name__ == "__main__":
    unittest.main()
