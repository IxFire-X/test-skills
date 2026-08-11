from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import yaml

from tools.run_tests import resolve_execution_context
from tools.skillsrc_manifest import SkillsrcError


class RunTestsSkillsrcV3Tests(unittest.TestCase):
    def _module(self, module_id: str, root: str, language: str, build_tool: str) -> dict:
        return {
            "id": module_id,
            "root": root,
            "stack": {"language": language, "build_tool": build_tool},
            "detected_from": [f"{root}/manifest"],
        }

    def _write_v3(self, root: Path, modules: list[dict]) -> Path:
        path = root / ".skillsrc"
        path.write_text(
            yaml.safe_dump(
                {
                    "version": "3.0",
                    "project": {"name": "platform"},
                    "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                    "modules": modules,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_resolves_selected_module_root_and_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            (root / "apps/web").mkdir(parents=True)
            manifest = self._write_v3(root, [
                self._module("api", "services/api", "python", "pip"),
                self._module("web", "apps/web", "typescript", "npm"),
            ])

            execution_root, language, selected = resolve_execution_context(root, manifest, "api", None)

            self.assertEqual(execution_root, (root / "services/api").resolve())
            self.assertEqual(language, "python")
            self.assertEqual(selected["id"], "api")

    def test_multiple_modules_without_selection_is_not_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._write_v3(root, [
                self._module("api", "services/api", "python", "pip"),
                self._module("web", "apps/web", "typescript", "npm"),
            ])

            with self.assertRaises(SkillsrcError) as raised:
                resolve_execution_context(root, manifest, None, None)

            self.assertEqual(raised.exception.code, "module_required")

    def test_language_override_must_match_selected_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services/api").mkdir(parents=True)
            manifest = self._write_v3(root, [self._module("api", "services/api", "python", "pip")])

            with self.assertRaises(SkillsrcError) as raised:
                resolve_execution_context(root, manifest, "api", "java")

            self.assertEqual(raised.exception.code, "language_conflict")


if __name__ == "__main__":
    unittest.main()
