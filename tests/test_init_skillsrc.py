import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from jsonschema import Draft202012Validator

from tools.init_skillsrc import ensure_skillsrc


class InitSkillsrcTests(unittest.TestCase):
    def _python_project(self, root):
        (root / "pyproject.toml").write_text('[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8")
        (root / "src").mkdir(); (root / "tests").mkdir()

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


if __name__ == "__main__":
    unittest.main()
