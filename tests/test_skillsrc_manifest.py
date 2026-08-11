import tempfile
import unittest
from pathlib import Path

import yaml

from tools.skillsrc_manifest import (
    SkillsrcError,
    load_skillsrc,
    normalize_skillsrc,
    resolve_module_root,
    select_module,
)


class SkillsrcManifestTests(unittest.TestCase):
    def _write(self, root: Path, value: dict) -> Path:
        path = root / ".skillsrc"
        path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def test_v2_normalizes_to_one_root_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write(root, {
                "version": "2.0",
                "project": {"name": "api", "language": "python", "framework": "fastapi", "build_tool": "pip"},
                "paths": {"source": "src", "tests": "tests"},
                "test": {"framework": "pytest"},
            })
            normalized = normalize_skillsrc(load_skillsrc(path))
            self.assertEqual([module["id"] for module in normalized["modules"]], ["root"])
            self.assertEqual(normalized["modules"][0]["stack"]["language"], "python")

    def test_v3_requires_explicit_module_when_multiple_exist(self):
        document = {
            "version": "3.0",
            "project": {"name": "platform"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [
                {"id": "api", "root": "services/api", "stack": {"language": "python", "build_tool": "pip"}, "detected_from": ["services/api/pyproject.toml"]},
                {"id": "web", "root": "apps/web", "stack": {"language": "typescript", "build_tool": "npm"}, "detected_from": ["apps/web/package.json"]},
            ],
        }
        normalized = normalize_skillsrc(document)
        with self.assertRaisesRegex(SkillsrcError, "module selection is required"):
            select_module(normalized, None)
        self.assertEqual(select_module(normalized, "web")["root"], "apps/web")

    def test_module_root_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillsrcError, "unsafe module root"):
                resolve_module_root(Path(temp), {"id": "bad", "root": "../outside"})

    def test_module_root_rejects_all_unsafe_path_forms(self):
        with tempfile.TemporaryDirectory() as temp:
            for root in ("/outside", r"\\server\share", r"C:outside", r"C:\outside", "api/../outside"):
                with self.subTest(root=root), self.assertRaisesRegex(SkillsrcError, "unsafe module root"):
                    resolve_module_root(Path(temp), {"id": "bad", "root": root})

    def test_load_skillsrc_reports_schema_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(Path(temp), {
                "version": "3.0",
                "project": {"name": "platform"},
                "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                "modules": [],
            })
            with self.assertRaisesRegex(SkillsrcError, "failed schema validation") as raised:
                load_skillsrc(path)
        self.assertEqual(raised.exception.code, "schema_invalid")
        self.assertTrue(raised.exception.details)

    def test_v3_loads_with_preserved_module_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(Path(temp), {
                "version": "3.0",
                "project": {"name": "platform", "methodology": "sdd"},
                "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                "modules": [{
                    "id": "api",
                    "root": "services/api",
                    "stack": {"language": "python", "framework": "fastapi", "build_tool": "pip"},
                    "paths": {"source": ["src"]},
                    "feature_sources": {"openapi": ["openapi.yaml"]},
                    "detected_from": ["services/api/pyproject.toml"],
                }],
            })
            normalized = normalize_skillsrc(load_skillsrc(path))
        self.assertEqual(normalized["modules"][0]["feature_sources"]["openapi"], ["openapi.yaml"])

    def test_module_root_resolves_within_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp)
            self.assertEqual(
                resolve_module_root(project_root, {"id": "api", "root": "services/api"}),
                (project_root / "services" / "api").resolve(),
            )

    def test_v2_optional_values_are_omitted(self):
        normalized = normalize_skillsrc({
            "project": {"name": "api", "language": "python"},
        })
        module = normalized["modules"][0]
        self.assertNotIn("framework", module["stack"])
        self.assertNotIn("build_tool", module["stack"])
        self.assertNotIn("paths", module)

    def test_v2_unknown_optional_values_are_omitted(self):
        normalized = normalize_skillsrc({
            "project": {"name": "api", "language": "python", "framework": "unknown"},
            "paths": {"source": "unknown"},
        })
        module = normalized["modules"][0]
        self.assertNotIn("framework", module["stack"])
        self.assertNotIn("paths", module)


if __name__ == "__main__":
    unittest.main()
