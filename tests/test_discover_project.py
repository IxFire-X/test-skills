from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.discover_project import discover_project, project_fingerprint


class DiscoverProjectTests(unittest.TestCase):
    def test_discovers_python_and_java_modules_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            (root / "services/api/pyproject.toml").write_text('[project]\nname="api"\ndependencies=["fastapi"]\n[project.optional-dependencies]\ntest=["pytest"]\n', encoding="utf-8")
            (root / "services/api/src").mkdir()
            (root / "services/api/tests").mkdir()
            (root / "services/orders/src/main/java").mkdir(parents=True)
            (root / "services/orders/pom.xml").write_text('<project><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency><dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>', encoding="utf-8")
            report = discover_project(root)
            self.assertEqual(report["status"], "ready")
            self.assertEqual([item["root"] for item in report["modules"]], ["services/api", "services/orders"])
            self.assertEqual(report["modules"][0]["stack"]["framework"], "fastapi")
            self.assertEqual(report["modules"][1]["test"]["framework"], "junit5")

    def test_multiple_test_frameworks_return_blocking_question(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"name":"web","devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            report = discover_project(root)
            self.assertEqual(report["status"], "needs_input")
            self.assertEqual(report["questions"][0]["field"], "modules.root.test.framework")
            self.assertEqual({option["value"] for option in report["questions"][0]["options"]}, {"jest", "mocha"})

    def test_workspaces_and_maven_aggregators_are_not_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "apps/web/src").mkdir(parents=True)
            (root / "apps/web/package.json").write_text('{"name":"web","dependencies":{"express":"1"}}', encoding="utf-8")
            (root / "java/service/src/main/java").mkdir(parents=True)
            (root / "java/service/pom.xml").write_text('<project><dependencies><dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>', encoding="utf-8")
            (root / "package.json").write_text('{"workspaces":["apps/*"]}', encoding="utf-8")
            (root / "pom.xml").write_text('<project><packaging>pom</packaging><modules><module>java/service</module></modules></project>', encoding="utf-8")
            report = discover_project(root)
            self.assertEqual([module["root"] for module in report["modules"]], ["apps/web", "java/service"])

    def test_ids_are_unique_for_same_directory_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in ("services/one/api", "services/two/api"):
                (root / directory).mkdir(parents=True)
                (root / directory / "pyproject.toml").write_text('[project]\nname="api"\n', encoding="utf-8")
            modules = discover_project(root)["modules"]
            self.assertEqual(len({module["id"] for module in modules}), 2)

    def test_no_manifest_is_error_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            report = discover_project(root)
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["modules"], [])
            self.assertEqual(before, after)

    def test_wrappers_are_reported_only_when_present(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "mvnw.cmd").write_text("", encoding="utf-8")
            (root / "gradlew").write_text("", encoding="utf-8")
            module = discover_project(root)["modules"][0]
            self.assertEqual(module["test"]["wrapper"], {"windows": "mvnw.cmd"})

    def test_secret_traversal_and_symlink_escape_never_enter_json(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            secret = "https://user:secret@example.invalid/private"
            (root / "package.json").write_text(json.dumps({"dependencies": {"internal": secret}}), encoding="utf-8")
            (root / ".env").write_text("TOKEN=top-secret", encoding="utf-8")
            (Path(outside) / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            rendered = json.dumps(discover_project(root), sort_keys=True)
            self.assertNotIn(secret, rendered)
            self.assertNotIn("top-secret", rendered)

    def test_symlink_escape_never_enters_json_when_supported(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            (root / "package.json").write_text('{"name":"safe"}', encoding="utf-8")
            (Path(outside) / "package.json").write_text('{"name":"escape"}', encoding="utf-8")
            try: (root / "escape").symlink_to(outside, target_is_directory=True)
            except OSError: self.skipTest("symlink creation unavailable")
            self.assertNotIn("escape/package.json", json.dumps(discover_project(root)))

    def test_catalog_marker_is_shared_with_scan_project(self):
        from tools import scan_project, stack_catalog
        marker = ("catalog-only-marker", "catalog-framework")
        stack_catalog.PYTHON_FRAMEWORK_MARKERS.append(marker)
        try:
            self.assertEqual(scan_project._match_markers("catalog-only-marker", scan_project.PYTHON_FRAMEWORK_MARKERS), "catalog-framework")
        finally: stack_catalog.PYTHON_FRAMEWORK_MARKERS.remove(marker)

    def test_semantic_option_ids_are_unique(self):
        from tools.discover_project import validate_report
        self.assertEqual(validate_report({"questions":[{"options":[{"id":"x"},{"id":"x"}]}]}), ["duplicate option id"])

    def test_go_work_is_evidence_only_and_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "app/src").mkdir(parents=True)
            (root / "app/go.mod").write_text("module app\n", encoding="utf-8")
            (root / "go.work").write_text("go 1.22\nuse ./app\n", encoding="utf-8")
            report = discover_project(root)
            self.assertEqual([m["root"] for m in report["modules"]], ["app"])
            before = report["fingerprint"]; (root / "go.work").write_text("go 1.22\nuse ./other\n", encoding="utf-8")
            self.assertNotEqual(before, discover_project(root)["fingerprint"])

    def test_encoded_ids_do_not_collide_with_root_or_punctuation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a_b", "a-b"):
                (root / name).mkdir(); (root / name / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            ids = [m["id"] for m in discover_project(root)["modules"]]
            self.assertEqual(len(ids), len(set(ids))); self.assertIn("root", ids)

    def test_malformed_toml_and_xml_are_sanitized_errors(self):
        for name, content in (("pyproject.toml", "[project\nsecret=https://u:p@x"), ("pom.xml", "<project>https://u:p@x")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                report = discover_project(Path(temp) / "missing") if False else None
                root = Path(temp); (root / name).write_text(content, encoding="utf-8")
                report = discover_project(root)
                self.assertEqual(report["status"], "error")
                self.assertNotIn("u:p@x", json.dumps(report))

    def test_confined_predicate_rejects_escape_and_conflict_omits_wrappers(self):
        from tools.discover_project import _ok
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); self.assertFalse(_ok(root, Path(outside) / "x"))
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "build.gradle").write_text("", encoding="utf-8")
            (root / "mvnw.cmd").write_text("", encoding="utf-8")
            module = discover_project(root)["modules"][0]
            self.assertNotIn("test", module)

    def test_option_evidence_names_exact_manifest_and_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "package.json").write_text('{"devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            options = discover_project(root)["questions"][0]["options"]
            self.assertEqual({e for o in options for e in o["evidence"]}, {"package.json:marker.jest", "package.json:marker.mocha"})

    def test_wrapper_follows_resolved_build_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"build.gradle").write_text("",encoding="utf-8"); (root/"mvnw.cmd").write_text("",encoding="utf-8"); (root/"gradlew.bat").write_text("",encoding="utf-8")
            self.assertEqual(discover_project(root)["modules"][0]["test"]["wrapper"], {"windows":"gradlew.bat"})

    def test_invalid_manifest_shapes_and_ready_unresolved_are_rejected(self):
        from tools.discover_project import validate_report
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"package.json").write_text("[]",encoding="utf-8")
            self.assertEqual(discover_project(root)["status"],"error")
        self.assertTrue(validate_report({"status":"ready","modules":[{"id":"root","stack":{}}],"questions":[]}))

    def test_marker_evidence_accumulates_all_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"pyproject.toml").write_text('[project]\ndependencies=["django","fastapi"]',encoding="utf-8"); (root/"requirements.txt").write_text("djangorestframework\n",encoding="utf-8")
            django=next(o for o in discover_project(root)["questions"] if o["field"]=="modules.root.stack.framework" for o in o["options"] if o["id"]=="django")
            self.assertEqual(django["evidence"],["pyproject.toml:marker.django","requirements.txt:marker.django"])

    def test_fingerprint_changes_only_when_evidence_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "pyproject.toml"
            manifest.write_text('[project]\nname="one"\n', encoding="utf-8")
            first = project_fingerprint(root, ["pyproject.toml"])
            self.assertEqual(first, project_fingerprint(root, ["pyproject.toml"]))
            manifest.write_text('[project]\nname="two"\n', encoding="utf-8")
            self.assertNotEqual(first, project_fingerprint(root, ["pyproject.toml"]))

    def test_report_is_deterministic_except_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "b/package.json").parent.mkdir(parents=True)
            (root / "a/package.json").parent.mkdir(parents=True)
            for name in ("a", "b"):
                (root / name / "package.json").write_text('{"devDependencies":{"mocha":"1","jest":"1"}}', encoding="utf-8")
            one, two = discover_project(root), discover_project(root)
            one.pop("scanned_at")
            two.pop("scanned_at")
            self.assertEqual(one, two)

    def test_scan_project_never_creates_skillsrc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text('[project]\nname="one"\n', encoding="utf-8")
            (root / "src.py").write_text("pass\n", encoding="utf-8")
            self.assertFalse((root / ".skillsrc").exists())
            script = Path(__file__).resolve().parents[1] / "tools" / "scan_project.py"
            result = subprocess.run([sys.executable, str(script), "--project", str(root), "--target", "src.py"], capture_output=True, check=False, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / ".skillsrc").exists())

    def test_nested_aggregators_workspace_inputs_and_fingerprint_are_evidence_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested/leaf/src").mkdir(parents=True)
            (root / "nested/leaf/package.json").write_text('{"name":"leaf"}', encoding="utf-8")
            (root / "pom.xml").write_text('<project><modules><module>nested</module></modules></project>', encoding="utf-8")
            (root / "nested/pom.xml").write_text('<project><modules><module>leaf</module></modules></project>', encoding="utf-8")
            (root / "settings.gradle").write_text("include ':nested:leaf'", encoding="utf-8")
            report = discover_project(root)
            self.assertEqual([m["root"] for m in report["modules"]], ["nested/leaf"])
            old = report["fingerprint"]
            (root / "settings.gradle").write_text("include ':changed'", encoding="utf-8")
            self.assertNotEqual(old, discover_project(root)["fingerprint"])

    def test_ambiguities_keep_module_and_stable_root_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "package.json").write_text('{"name":"mutable"}', encoding="utf-8")
            report = discover_project(root)
            self.assertEqual(report["status"], "needs_input")
            self.assertEqual(report["modules"][0]["id"], "root")
            self.assertNotIn("language", report["modules"][0]["stack"])

    def test_build_tool_conflict_and_malformed_input_block_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "build.gradle").write_text("", encoding="utf-8")
            report = discover_project(root)
            self.assertEqual(report["status"], "needs_input")
            self.assertNotIn("build_tool", report["modules"][0]["stack"])
            (root / "package.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(discover_project(root)["status"], "error")
