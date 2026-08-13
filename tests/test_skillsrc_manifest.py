from copy import deepcopy
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

    def _v3(self) -> dict:
        return {
            "version": "3.0",
            "project": {"name": "platform"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{
                "id": "api",
                "root": "services/api",
                "stack": {"language": "python", "build_tool": "pip"},
                "detected_from": ["services/api/pyproject.toml"],
            }],
        }

    def _assert_load_path_error(self, document: dict, expected_path: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillsrcError, "unsafe path") as raised:
                load_skillsrc(self._write(Path(temp), document))
        self.assertEqual(raised.exception.code, "unsafe_path")
        self.assertEqual(raised.exception.details[0]["path"], expected_path)

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
        document = self._v3()
        document["modules"].append({
            "id": "web",
            "root": "apps/web",
            "stack": {"language": "typescript", "build_tool": "npm"},
            "detected_from": ["apps/web/package.json"],
        })
        normalized = normalize_skillsrc(document)
        with self.assertRaisesRegex(SkillsrcError, "module selection is required"):
            select_module(normalized, None)
        self.assertEqual(select_module(normalized, "web")["root"], "apps/web")

    def test_duplicate_module_ids_are_rejected_on_load_and_normalization(self):
        document = self._v3()
        document["modules"].append(deepcopy(document["modules"][0]))
        document["modules"][1]["root"] = "services/duplicate"
        document["modules"][1]["detected_from"] = ["services/duplicate/pyproject.toml"]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillsrcError, "duplicate module id") as raised:
                load_skillsrc(self._write(Path(temp), document))
        self.assertEqual(raised.exception.code, "duplicate_module_id")
        self.assertEqual(raised.exception.details[0]["id"], "api")
        self.assertEqual(raised.exception.details[0]["indices"], [0, 1])
        with self.assertRaisesRegex(SkillsrcError, "duplicate module id"):
            normalize_skillsrc(document)

    def test_select_module_reports_ambiguous_duplicate_as_defensive_fallback(self):
        normalized = {
            "project": {"name": "platform"},
            "modules": [{"id": "api"}, {"id": "api"}],
        }
        with self.assertRaisesRegex(SkillsrcError, "ambiguous module") as raised:
            select_module(normalized, "api")
        self.assertEqual(raised.exception.code, "module_ambiguous")

    def test_module_root_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SkillsrcError, "unsafe module root"):
                resolve_module_root(Path(temp), {"id": "bad", "root": "../outside"})

    def test_module_root_rejects_all_unsafe_path_forms(self):
        with tempfile.TemporaryDirectory() as temp:
            for root in ("/outside", r"\\server\share", r"\rooted", r"C:outside", r"C:\outside", "api/../outside"):
                with self.subTest(root=root), self.assertRaisesRegex(SkillsrcError, "unsafe module root"):
                    resolve_module_root(Path(temp), {"id": "bad", "root": root})

    def test_load_rejects_unsafe_path_forms(self):
        for root in ("/outside", r"\\server\share", r"\rooted", r"C:outside", r"C:\outside", "api/../outside"):
            with self.subTest(root=root):
                document = self._v3()
                document["modules"][0]["root"] = root
                self._assert_load_path_error(document, "/modules/0/root")

    def test_load_rejects_unsafe_path_fields(self):
        cases = [
            ({"project": {"name": "api", "language": "python"}, "paths": {"source": "/outside"}}, "/paths/source"),
            ({"project": {"name": "api", "language": "python"}, "sdd": {"openapi": "/outside"}}, "/sdd/openapi"),
        ]
        for document, expected_path in cases:
            with self.subTest(expected_path=expected_path):
                self._assert_load_path_error(document, expected_path)

        v3_cases = [
            ("paths", {"source": ["/outside"]}, "/modules/0/paths/source/0"),
            ("feature_sources", {"openapi": ["/outside"]}, "/modules/0/feature_sources/openapi/0"),
            ("detected_from", ["/outside"], "/modules/0/detected_from/0"),
            ("test", {"wrapper": {"windows": "/outside", "linux": "scripts/test.sh"}}, "/modules/0/test/wrapper/windows"),
            ("test", {"wrapper": {"windows": "scripts/test.cmd", "linux": "/outside"}}, "/modules/0/test/wrapper/linux"),
        ]
        for key, value, expected_path in v3_cases:
            with self.subTest(expected_path=expected_path):
                document = self._v3()
                document["modules"][0][key] = value
                self._assert_load_path_error(document, expected_path)

        for key in ("path", "skill_file", "input_contract", "output_contract"):
            with self.subTest(skills_registry_key=key):
                document = self._v3()
                skill = {"name": "example", "path": "skills/example", "description": "Example"}
                skill[key] = "/outside"
                document["skills_registry"] = {"example": skill}
                self._assert_load_path_error(document, f"/skills_registry/example/{key}")

    def test_load_accepts_safe_windows_and_posix_relative_paths(self):
        document = self._v3()
        document["modules"][0].update({
            "root": r"services\api",
            "paths": {"source": [r"src\main", "src/lib"]},
            "feature_sources": {"openapi": ["docs/openapi.yaml"]},
            "detected_from": [r"services\api\pyproject.toml"],
            "test": {"wrapper": {"windows": r"scripts\test.cmd", "linux": "scripts/test.sh"}},
        })
        document["skills_registry"] = {
            "example": {
                "name": "example",
                "path": r"skills\example",
                "skill_file": "SKILL.md",
                "input_contract": "contracts/input.md",
                "output_contract": "contracts/output.md",
                "description": "Example",
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(load_skillsrc(self._write(Path(temp), document))["modules"][0]["root"], r"services\api")

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

    def test_load_skillsrc_wraps_malformed_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".skillsrc"
            path.write_text("project: [unclosed", encoding="utf-8")
            with self.assertRaisesRegex(SkillsrcError, "invalid YAML") as raised:
                load_skillsrc(path)
        self.assertEqual(raised.exception.code, "invalid_yaml")
        self.assertTrue(raised.exception.details)

    def test_v3_normalization_preserves_document_metadata_and_deep_copies(self):
        document = self._v3()
        document.update({
            "resolution": {"skillsrc_vs_scan": "merge"},
            "contracts": {"status_markers_version": "1"},
            "skills_registry": {
                "example": {"name": "example", "path": "skills/example", "description": "Example", "tags": ["@tool"]}
            },
        })
        document["modules"][0]["paths"] = {"source": ["src"]}
        normalized = normalize_skillsrc(document)
        self.assertEqual(set(normalized), {"project", "modules"})
        self.assertEqual(normalized["project"]["name"], "platform")
        for key in ("version", "discovery", "resolution", "contracts", "skills_registry"):
            self.assertEqual(normalized["project"][key], document[key])

        normalized["project"]["discovery"]["on_missing"] = "changed"
        normalized["modules"][0]["paths"]["source"].append("generated")
        document["skills_registry"]["example"]["tags"].append("@source")
        self.assertEqual(document["discovery"]["on_missing"], "automatic")
        self.assertEqual(document["modules"][0]["paths"]["source"], ["src"])
        self.assertEqual(normalized["project"]["skills_registry"]["example"]["tags"], ["@tool"])

    def test_v3_loads_with_preserved_module_values(self):
        document = self._v3()
        document["modules"][0].update({
            "stack": {"language": "python", "framework": "fastapi", "build_tool": "pip"},
            "paths": {"source": ["src"]},
            "feature_sources": {"openapi": ["openapi.yaml"]},
        })
        with tempfile.TemporaryDirectory() as temp:
            normalized = normalize_skillsrc(load_skillsrc(self._write(Path(temp), document)))
        self.assertEqual(normalized["modules"][0]["feature_sources"]["openapi"], ["openapi.yaml"])

    def test_module_root_resolves_within_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp)
            self.assertEqual(
                resolve_module_root(project_root, {"id": "api", "root": "services/api"}),
                (project_root / "services" / "api").resolve(),
            )

    def test_v2_optional_values_are_omitted_when_absent(self):
        normalized = normalize_skillsrc({
            "project": {"name": "api", "language": "python"},
        })
        module = normalized["modules"][0]
        self.assertNotIn("framework", module["stack"])
        self.assertNotIn("build_tool", module["stack"])
        self.assertNotIn("paths", module)

    def test_v2_preserves_empty_and_unknown_present_values(self):
        normalized = normalize_skillsrc({
            "project": {"name": "api", "language": "python", "framework": "unknown"},
            "paths": {"source": "", "tests": "unknown"},
            "sdd": {"openapi": "", "domain": "unknown"},
            "test": {"api_client": "", "database": "unknown"},
        })
        module = normalized["modules"][0]
        self.assertEqual(module["stack"]["framework"], "unknown")
        self.assertEqual(module["paths"], {"source": [""], "tests": ["unknown"]})
        self.assertEqual(module["feature_sources"], {"openapi": [""], "domain": ["unknown"]})
        self.assertEqual(module["test"], {"api_client": "", "database": "unknown"})


if __name__ == "__main__":
    unittest.main()
