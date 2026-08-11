from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tools import run_tests
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

    def _write_v2(self, root: Path) -> Path:
        path = root / ".skillsrc"
        path.write_text(
            yaml.safe_dump({"project": {"name": "api", "language": "python"}}, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def _run_main(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with patch("sys.argv", ["run_tests.py", *arguments]), patch("sys.stdout", output):
            exit_code = run_tests.main()
        return exit_code, json.loads(output.getvalue())

    @staticmethod
    def _missing_python_environment(project_dir: str, _python_executable: str | None) -> dict:
        return {
            "status": "missing",
            "interpreter": None,
            "interpreter_path": None,
            "working_dir": project_dir,
            "missing": ["python"],
            "_interpreter_bin": None,
        }

    @staticmethod
    def _ready_python_environment(project_dir: str, _python_executable: str | None) -> dict:
        return {
            "status": "ready",
            "interpreter": "Python 3.11",
            "interpreter_path": "python",
            "working_dir": project_dir,
            "missing": None,
            "_interpreter_bin": "python",
        }

    @staticmethod
    def _passing_python_result() -> dict:
        return {
            "verdict": "PASS",
            "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": "python -m pytest"},
            "stats": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0},
            "failed_methods": None,
            "root_cause": [],
            "raw_output_excerpt": "",
            "exit_code": 0,
        }

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

    def test_main_passes_selected_module_root_to_artifact_loader_and_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module_root = root / "services/api"
            module_root.mkdir(parents=True)
            self._write_v3(root, [self._module("api", "services/api", "python", "pip")])
            with patch.object(run_tests, "load_automation_artifact", return_value=(None, None)) as artifact_loader, \
                 patch.object(run_tests, "check_python_env", side_effect=self._ready_python_environment), \
                 patch.object(run_tests, "run_python_pytest", return_value=self._passing_python_result()) as runner:
                exit_code, report = self._run_main(
                    "--project", str(root), "--module", "api", "--automation-artifact", "artifact.json"
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(report["verdict"], "PASS")
            artifact_loader.assert_called_once_with("artifact.json", str(module_root.resolve()))
            runner.assert_called_once_with(str(module_root.resolve()), "python", None, None)

    def test_main_keeps_v2_root_as_execution_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_v2(root)
            with patch.object(run_tests, "check_python_env", side_effect=self._missing_python_environment) as environment:
                exit_code, report = self._run_main("--project", str(root))

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["verdict"], "NOT_RUNNABLE")
            environment.assert_called_once_with(str(root.resolve()), None)

    def test_main_falls_back_to_project_root_when_manifest_is_absent_with_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(run_tests, "check_python_env", side_effect=self._missing_python_environment) as environment:
                exit_code, report = self._run_main("--project", str(root), "--language", "python")

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["verdict"], "NOT_RUNNABLE")
            environment.assert_called_once_with(str(root.resolve()), None)

    def test_main_falls_back_to_project_root_when_manifest_cannot_be_read_with_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._write_v2(root)
            with patch.object(run_tests, "load_skillsrc", side_effect=OSError("permission denied")), \
                 patch.object(run_tests, "check_python_env", side_effect=self._missing_python_environment) as environment:
                exit_code, report = self._run_main(
                    "--project", str(root), "--skillsrc", str(manifest), "--language", "python"
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["verdict"], "NOT_RUNNABLE")
            environment.assert_called_once_with(str(root.resolve()), None)

    def test_main_reports_unreadable_manifest_without_language_as_language_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._write_v2(root)
            with patch.object(run_tests, "load_skillsrc", side_effect=OSError("permission denied")):
                exit_code, report = self._run_main("--project", str(root), "--skillsrc", str(manifest))

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["verdict"], "NOT_RUNNABLE")
            self.assertEqual(report["environment"]["missing"], ["language_detection"])


if __name__ == "__main__":
    unittest.main()
