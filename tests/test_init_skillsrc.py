import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from jsonschema import Draft202012Validator

from tools import init_skillsrc
from tools.init_skillsrc import _atomic_json, _confined_output, ensure_skillsrc


class InitSkillsrcTests(unittest.TestCase):
    def _python_project(self, root):
        (root / "pyproject.toml").write_text('[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8")
        (root / "src").mkdir(); (root / "tests").mkdir()

    def _make_directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError as symlink_error:
            if sys.platform != "win32":
                self.skipTest(f"directory symlinks unavailable: {symlink_error}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"directory links unavailable: {result.stderr or result.stdout}")

    def _remove_directory_link(self, link):
        if not link.exists():
            return
        try:
            link.unlink()
        except OSError:
            link.rmdir()

    def test_missing_manifest_is_created_and_second_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            first = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(first["status"], "created")
            original = (root / ".skillsrc").read_bytes()
            second = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_critical_question_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"name":"web","devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            self.assertEqual(ensure_skillsrc(root, {}, write=True)["status"], "needs_input")
            self.assertFalse((root / ".skillsrc").exists())

    def test_preview_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            self.assertEqual(ensure_skillsrc(root, {}, write=False)["status"], "preview")
            self.assertFalse((root / ".skillsrc").exists())

    def test_unknown_answer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "package.json").write_text('{"devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            report = ensure_skillsrc(root, {"module:root:test.framework": "invented"}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertFalse((root / ".skillsrc").exists())

    def test_existing_bytes_survive_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            self.assertEqual(ensure_skillsrc(root, {}, write=True)["status"], "created")
            original = (root / ".skillsrc").read_bytes()
            (root / "worker").mkdir(); (root / "worker" / "package.json").write_text("{}", encoding="utf-8")
            with mock.patch("tools.init_skillsrc.os.replace", side_effect=OSError("denied")):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_v3_additive_module_update_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            initial = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(initial["status"], "created")
            project_name = initial["module_ids"] and root.name
            (root / "worker").mkdir(); (root / "worker" / "package.json").write_text('{"name":"worker"}', encoding="utf-8")
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "updated")
            self.assertEqual(report["module_ids"], ["root", "root--776f726b6572"])
            self.assertIn(f'name: {project_name}', (root / ".skillsrc").read_text(encoding="utf-8"))

    def test_destructive_change_requires_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            content = (root / ".skillsrc").read_text(encoding="utf-8").replace('language: python', 'language: go')
            (root / ".skillsrc").write_text(content, encoding="utf-8")
            before = (root / ".skillsrc").read_bytes(); report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "conflict")
            self.assertEqual((root / ".skillsrc").read_bytes(), before)

    def test_reconciliation_use_detected_replaces_language(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("language: python", "language: go"), encoding="utf-8")
            report = ensure_skillsrc(root, {"replace:modules.root.stack.language": "use-detected"}, write=True)
            self.assertEqual(report["status"], "updated")
            self.assertIn("language: python", skillsrc.read_text(encoding="utf-8"))

    def test_keep_existing_conflict_still_adds_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("language: python", "language: go"), encoding="utf-8")
            (root / "worker").mkdir(); (root / "worker" / "package.json").write_text("{}", encoding="utf-8")
            report = ensure_skillsrc(root, {"replace:modules.root.stack.language": "keep-existing"}, write=True)
            self.assertEqual(report["status"], "updated")
            text = skillsrc.read_text(encoding="utf-8")
            self.assertIn("language: go", text); self.assertIn("root--776f726b6572", text)

    def test_removal_use_detected_removes_module_and_stale_answer_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            text = skillsrc.read_text(encoding="utf-8").replace("modules:\n", "modules:\n- id: obsolete\n  root: obsolete\n  stack:\n    language: python\n  detected_from:\n  - pyproject.toml\n")
            skillsrc.write_text(text, encoding="utf-8")
            conflict = ensure_skillsrc(root, {}, write=True); self.assertEqual(conflict["status"], "conflict")
            answer = conflict["questions"][0]["id"]
            self.assertEqual(ensure_skillsrc(root, {answer: "use-detected"}, write=True)["status"], "updated")
            self.assertNotIn("obsolete", skillsrc.read_text(encoding="utf-8"))
            before = skillsrc.read_bytes()
            self.assertEqual(ensure_skillsrc(root, {"replace:stale": "use-detected"}, write=True)["status"], "error")
            self.assertEqual(skillsrc.read_bytes(), before)

    def test_secret_module_never_appears_in_conflict_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            text = skillsrc.read_text(encoding="utf-8").replace("modules:\n", "modules:\n- id: private\n  root: private\n  stack:\n    language: python\n  detected_from:\n  - pyproject.toml\n")
            skillsrc.write_text(text, encoding="utf-8")
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "conflict")
            self.assertNotIn("secret", json.dumps(report))

    def test_invalid_reconciliation_option_precedes_missing_question(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            text = skillsrc.read_text(encoding="utf-8").replace("language: python", "language: go")
            text = text.replace("modules:\n", "modules:\n- id: obsolete\n  root: obsolete\n  stack:\n    language: python\n  detected_from:\n  - pyproject.toml\n")
            skillsrc.write_text(text, encoding="utf-8")
            report = ensure_skillsrc(root, {"replace:modules.root.stack.language": "bad"}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["errors"], ["answer_unknown"])

    def test_v2_declared_framework_mismatch_and_unsupported_field_block_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            mismatch = b'project:\n  name: api\n  language: python\n  framework: django\n  build_tool: pip\n'
            skillsrc = root / ".skillsrc"; skillsrc.write_bytes(mismatch)
            self.assertEqual(ensure_skillsrc(root, {}, write=True)["status"], "conflict")
            self.assertEqual(skillsrc.read_bytes(), mismatch)
            unsupported = b'project:\n  name: api\n  language: python\n  build_tool: pip\n  type: microservice\n'
            skillsrc.write_bytes(unsupported)
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(skillsrc.read_bytes(), unsupported)

    def test_matching_v2_rejects_stale_reconciliation_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            original = b'project:\n  name: api\n  language: python\n  build_tool: pip\n'
            (root / ".skillsrc").write_bytes(original)
            report = ensure_skillsrc(root, {"replace:stale": "use-detected"}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_receipt_schema_rejects_invalid_status_arrays(self):
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
        bad = {"status": "created", "skillsrc_path": ".skillsrc", "written": False, "module_ids": [], "questions": [], "changes": [], "warnings": [], "errors": [], "discovery_fingerprint": "0" * 64}
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(bad)))

    def test_workspace_evidence_fingerprint_create_and_membership_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "apps" / "leaf" / "src").mkdir(parents=True)
            (root / "package.json").write_text('{"workspaces":["apps/*"]}', encoding="utf-8")
            (root / "apps" / "leaf" / "package.json").write_text('{"name":"leaf"}', encoding="utf-8")
            self.assertEqual(ensure_skillsrc(root, {}, write=True)["status"], "created")
            (root / ".skillsrc").unlink()
            original = __import__("tools.init_skillsrc", fromlist=["discover_project"]).discover_project
            calls = 0
            def mutate_then_report(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    (root / "package.json").write_text('{"workspaces":["changed/*"]}', encoding="utf-8")
                return original(path)
            with mock.patch("tools.init_skillsrc.discover_project", side_effect=mutate_then_report):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["errors"], ["project_changed"])
            self.assertFalse((root / ".skillsrc").exists())

    def test_destination_sentinel_after_final_fingerprint_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            original = __import__("tools.init_skillsrc", fromlist=["discover_project"]).discover_project
            calls = 0
            def mutate_destination(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    (root / ".skillsrc").write_bytes(b"sentinel")
                return original(path)
            with mock.patch("tools.init_skillsrc.discover_project", side_effect=mutate_destination):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["errors"], ["destination_changed"])
            self.assertEqual((root / ".skillsrc").read_bytes(), b"sentinel")

    def test_receipt_confinement_rejects_symlink_escape_and_replace_failure_preserves_target(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); target = root / "docs" / "to_do" / "receipt.json"
            (root / "docs").mkdir(); target.parent.mkdir()
            target.write_text("old", encoding="utf-8")
            with mock.patch("tools.init_skillsrc._replace_output", side_effect=OSError("denied")):
                with self.assertRaises(OSError): _atomic_json(root, target, {"status": "preview"})
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            try:
                (root / "docs" / "to_do").rmdir(); (root / "docs" / "to_do").symlink_to(outside, target_is_directory=True)
            except OSError:
                return
            with self.assertRaises(ValueError):
                _atomic_json(root, target, {"status": "preview"})

    def test_v2_matching_module_stays_v2(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            original = b'project:\n  name: api\n  language: python\n  build_tool: pip\n'
            (root / ".skillsrc").write_bytes(original)
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "unchanged")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_v2_multimodule_requires_migration_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); (root / "worker").mkdir(); (root / "worker" / "package.json").write_text('{}', encoding="utf-8")
            original = b'project:\n  name: api\n  language: python\n  build_tool: pip\n'
            (root / ".skillsrc").write_bytes(original)
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "conflict")
            self.assertEqual(report["questions"][0]["id"], "migrate-v2-to-v3")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)

    def test_v2_migration_choices_preserve_extensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            (root / "worker").mkdir(); (root / "worker" / "package.json").write_text("{}", encoding="utf-8")
            original = b'project:\n  name: preserved\n  language: python\n  build_tool: pip\nmethodology: tdd\nresolution:\n  status_conflict: ask_user\n'
            skillsrc = root / ".skillsrc"; skillsrc.write_bytes(original)
            self.assertEqual(ensure_skillsrc(root, {"migrate-v2-to-v3": "keep-existing"}, write=True)["status"], "unchanged")
            self.assertEqual(skillsrc.read_bytes(), original)
            report = ensure_skillsrc(root, {"migrate-v2-to-v3": "use-detected"}, write=True)
            self.assertEqual(report["status"], "updated")
            migrated = skillsrc.read_text(encoding="utf-8")
            self.assertIn("name: preserved", migrated); self.assertIn("methodology: tdd", migrated)
            self.assertIn("status_conflict: ask_user", migrated); self.assertIn("root--776f726b6572", migrated)

    def test_fingerprint_drift_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root)
            with mock.patch("tools.init_skillsrc.atomic_write_skillsrc", side_effect=Exception("project_changed")):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertFalse((root / ".skillsrc").exists())

    def test_output_must_be_under_docs_to_do(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
            for output in (root / "receipt.json", root / "docs" / "to_do" / ".." / "bad.json", Path("C:/receipt.json"), Path("//server/share/x.json")):
                result = subprocess.run([sys.executable, str(script), "--project", str(root), "--output", str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)

    def test_invalid_existing_yaml_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); bad = b'project: [not valid\n'
            (root / ".skillsrc").write_bytes(bad)
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual((root / ".skillsrc").read_bytes(), bad)

    def test_repeated_write_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            first = (root / ".skillsrc").read_bytes(); ensure_skillsrc(root, {}, write=True)
            self.assertEqual((root / ".skillsrc").read_bytes(), first)

    def test_receipt_redacts_registry_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); (root / "docs" / "to_do").mkdir(parents=True)
            script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"; output = root / "docs" / "to_do" / "skillsrc-init.json"
            (root / "package.json").write_text('{"dependencies":{"private":"https://u:secret@example.invalid/x"}}', encoding="utf-8")
            result = subprocess.run([sys.executable, str(script), "--project", str(root), "--output", str(output)], capture_output=True, text=True)
            self.assertNotIn("u:secret", result.stdout + output.read_text(encoding="utf-8"))
            schema = json.loads((Path(__file__).parents[1] / "schemas" / "skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
            self.assertFalse(list(Draft202012Validator(schema).iter_errors(json.loads(output.read_text(encoding="utf-8")))))


    def test_schema_enforces_each_status_array_contract(self):
        """A status branch may not carry arrays from another status."""
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        base = {"skillsrc_path": ".skillsrc", "module_ids": ["root"], "questions": [], "changes": [], "warnings": [], "errors": [], "discovery_fingerprint": "a" * 64}
        valid = [
            {**base, "status": "created", "written": True, "changes": [{"operation": "create", "path": ".skillsrc"}]},
            {**base, "status": "updated", "written": True, "changes": [{"operation": "update", "path": ".skillsrc"}]},
            {**base, "status": "unchanged", "written": False},
            {**base, "status": "preview", "written": False, "changes": [{"operation": "update", "path": ".skillsrc"}]},
            {**base, "status": "needs_input", "written": False, "questions": [{"id": "q", "field": "f", "impact": "i", "options": [{"id": "python", "value": "python", "evidence": []}]}]},
            {**base, "status": "conflict", "written": False, "questions": [{"id": "q", "field": "f", "impact": "i", "operation": "replace", "options": [{"id": "keep-existing", "value": "keep-existing", "evidence": []}]}]},
            {**base, "status": "error", "written": False, "errors": ["read_error"]},
        ]
        for artifact in valid:
            self.assertFalse(list(validator.iter_errors(artifact)), artifact["status"])
        invalid = [
            {**valid[0], "questions": valid[4]["questions"]}, {**valid[1], "errors": ["write_error"]},
            {**valid[2], "questions": valid[4]["questions"]}, {**valid[3], "questions": valid[4]["questions"]},
            {**valid[3], "errors": ["write_error"]}, {**valid[4], "changes": valid[1]["changes"]},
            {**valid[4], "errors": ["write_error"]}, {**valid[5], "changes": valid[1]["changes"]},
            {**valid[5], "errors": ["write_error"]}, {**valid[6], "questions": valid[4]["questions"]},
            {**valid[6], "changes": valid[1]["changes"]},
        ]
        for artifact in invalid:
            self.assertTrue(list(validator.iter_errors(artifact)), artifact["status"])

    def test_temp_manifest_validation_failure_preserves_old_bytes_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            original = (root / ".skillsrc").read_bytes()
            (root / "worker").mkdir(); (root / "worker" / "package.json").write_text("{}", encoding="utf-8")
            real_load = init_skillsrc.load_skillsrc
            def reject_temporary(path):
                if str(path).endswith(".tmp"):
                    raise init_skillsrc.InitError("temp_invalid", "temporary manifest failed validation")
                return real_load(path)
            with mock.patch("tools.init_skillsrc.load_skillsrc", side_effect=reject_temporary):
                report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "error")
            self.assertEqual((root / ".skillsrc").read_bytes(), original)
            self.assertEqual(list(root.glob(".skillsrc.*.tmp")), [])

    def test_repeated_discovery_answer_update_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            answer = {"module:root:test.framework": "jest"}
            self.assertEqual(ensure_skillsrc(root, answer, write=True)["status"], "created")
            first = (root / ".skillsrc").read_bytes()
            self.assertEqual(ensure_skillsrc(root, answer, write=True)["status"], "unchanged")
            self.assertEqual((root / ".skillsrc").read_bytes(), first)

    def test_framework_credential_never_reaches_conflict_or_cli_receipt(self):
        credential = "https://user:secret@example.invalid/framework"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); ensure_skillsrc(root, {}, write=True)
            skillsrc = root / ".skillsrc"
            obsolete = "- id: private\n  root: private\n  stack:\n    language: python\n    framework: " + credential + "\n  detected_from:\n  - pyproject.toml\n"
            skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("modules:\n", "modules:\n" + obsolete), encoding="utf-8")
            report = ensure_skillsrc(root, {}, write=True)
            self.assertEqual(report["status"], "conflict")
            self.assertNotIn("user:secret", json.dumps(report))
            (root / "docs" / "to_do").mkdir(parents=True)
            output = root / "docs" / "to_do" / "receipt.json"; script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
            process = subprocess.run([sys.executable, str(script), "--project", str(root), "--write", "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 3)
            self.assertNotIn("user:secret", process.stdout + output.read_text(encoding="utf-8"))

    def test_main_reports_successful_manifest_when_receipt_write_fails_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); (root / "docs" / "to_do").mkdir(parents=True)
            output = root / "docs" / "to_do" / "receipt.json"; stdout, stderr = __import__("io").StringIO(), __import__("io").StringIO()
            with mock.patch.object(sys, "argv", ["init_skillsrc.py", "--project", str(root), "--write", "--output", str(output)]), mock.patch("tools.init_skillsrc._atomic_json", side_effect=ValueError("receipt parent changed")), mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
                self.assertEqual(init_skillsrc.main(), 1)
            self.assertTrue((root / ".skillsrc").exists())
            self.assertEqual(json.loads(stdout.getvalue())["status"], "created")
            self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())

    def test_cli_exit_categories_and_schema_invalid_existing_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
            self.assertEqual(subprocess.run([sys.executable, str(script), "--project", str(root)], capture_output=True, text=True).returncode, 0)
            bad_answers = root / "answers.json"; bad_answers.write_text("[]", encoding="utf-8")
            self.assertEqual(subprocess.run([sys.executable, str(script), "--project", str(root), "--answers", str(bad_answers)], capture_output=True, text=True).returncode, 2)
            bad = b'version: "3.0"\nproject: []\n'; (root / ".skillsrc").write_bytes(bad)
            invalid_existing = subprocess.run([sys.executable, str(script), "--project", str(root), "--write"], capture_output=True, text=True)
            self.assertEqual(invalid_existing.returncode, 2)
            self.assertEqual((root / ".skillsrc").read_bytes(), bad)
            self.assertNotIn("project: []", invalid_existing.stdout)
        with tempfile.TemporaryDirectory() as temp:
            conflict_root = Path(temp)
            (conflict_root / "package.json").write_text('{"devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8")
            self.assertEqual(subprocess.run([sys.executable, str(script), "--project", str(conflict_root), "--write"], capture_output=True, text=True).returncode, 3)

    def test_receipt_parent_identity_change_is_blocked_without_external_write(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); destination = root / "docs" / "to_do" / "receipt.json"; destination.parent.mkdir(parents=True)
            external = Path(outside) / "receipt.json"
            real_check = init_skillsrc._verify_output_parent
            moved = root / "docs" / "old_to_do"
            calls = 0
            def replace_parent(record, temporary):
                nonlocal calls
                calls += 1
                if calls == 1:
                    try:
                        record.parent.rename(moved)
                    except OSError:
                        pass
                    else:
                        record.parent.mkdir()
                return real_check(record, temporary)
            with mock.patch("tools.init_skillsrc._verify_output_parent", side_effect=replace_parent):
                _atomic_json(root, destination, {"status": "preview"})
            self.assertTrue(destination.exists())
            self.assertFalse(external.exists())
            self.assertFalse(moved.exists())
            self.assertEqual(list(destination.parent.glob(".skillsrc-init.*.tmp")), [])

    def test_output_ancestor_link_with_missing_child_has_no_external_side_effect(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); self._python_project(root)
            external = Path(outside); link = root / "docs"
            self._make_directory_link(link, external)
            try:
                output = link / "to_do" / "receipt.json"
                script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
                process = subprocess.run(
                    [sys.executable, str(script), "--project", str(root), "--write", "--output", str(output)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertFalse((root / ".skillsrc").exists())
                self.assertFalse((external / "to_do").exists())
                self.assertNotIn("Traceback", process.stdout + process.stderr)
            finally:
                self._remove_directory_link(link)

    def test_receipt_parent_cannot_be_swapped_after_final_check_before_replace(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); destination = root / "docs" / "to_do" / "receipt.json"
            destination.parent.mkdir(parents=True)
            moved = root / "docs" / "moved_to_do"; external = Path(outside)
            real_replace = init_skillsrc._replace_output
            swapped = []

            def swap_parent_then_replace(record, temporary, replacement_handle):
                try:
                    destination.parent.rename(moved)
                except OSError:
                    swapped.append(False)
                else:
                    self._make_directory_link(destination.parent, external)
                    swapped.append(True)
                return real_replace(record, temporary, replacement_handle)

            try:
                with mock.patch("tools.init_skillsrc._replace_output", side_effect=swap_parent_then_replace):
                    _atomic_json(root, destination, {"status": "preview"})
                self.assertEqual(swapped, [False])
                self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"status": "preview"})
                self.assertFalse((external / "receipt.json").exists())
            finally:
                if destination.parent.is_symlink() or (destination.parent.exists() and moved.exists()):
                    self._remove_directory_link(destination.parent)
                if moved.exists() and not destination.parent.exists():
                    moved.rename(destination.parent)

    def test_malformed_discovery_manifest_exits_2_with_sanitized_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "package.json").write_text('{"dependencies": {"private": "https://user:secret@example.invalid",', encoding="utf-8")
            output = root / "docs" / "to_do" / "receipt.json"; output.parent.mkdir(parents=True)
            script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
            process = subprocess.run(
                [sys.executable, str(script), "--project", str(root), "--write", "--output", str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
            receipt_text = output.read_text(encoding="utf-8")
            self.assertNotIn("user:secret", process.stdout + process.stderr + receipt_text)
            self.assertNotIn("Traceback", process.stdout + process.stderr)
            self.assertEqual(json.loads(receipt_text)["status"], "error")
            self.assertFalse((root / ".skillsrc").exists())

    def test_destination_read_failure_exits_2_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._python_project(root); (root / ".skillsrc").mkdir()
            output = root / "docs" / "to_do" / "receipt.json"; output.parent.mkdir(parents=True)
            script = Path(__file__).parents[1] / "tools" / "init_skillsrc.py"
            process = subprocess.run(
                [sys.executable, str(script), "--project", str(root), "--write", "--output", str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
            self.assertNotIn("Traceback", process.stdout + process.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["errors"], ["read_error"])
            self.assertTrue((root / ".skillsrc").is_dir())

if __name__ == "__main__":
    unittest.main()
