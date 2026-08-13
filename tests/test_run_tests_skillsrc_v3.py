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
    def _passing_v3_result() -> dict:
        return {"verdict": "PASS"}

    def _run_v3_main(self, root: Path, *arguments: str):
        document = {"document": "canonical"}
        artifact = {"artifact": "automation"}
        with patch(
            "tools.canonical_document.load_canonical_document",
            return_value=document,
        ) as document_loader, patch.object(
            run_tests,
            "load_automation_artifact",
            return_value=artifact,
        ) as artifact_loader, patch.object(
            run_tests,
            "run_tests_v3",
            return_value=self._passing_v3_result(),
        ) as runner:
            exit_code, report = self._run_main(
                "--project",
                str(root),
                "--canonical-document",
                "canonical.json",
                "--automation-artifact",
                "automation.json",
                *arguments,
            )
        return exit_code, report, document, artifact, document_loader, artifact_loader, runner

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
            exit_code, report, document, artifact, document_loader, artifact_loader, runner = (
                self._run_v3_main(root, "--module", "api")
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(report["verdict"], "PASS")
            document_loader.assert_called_once_with(Path("canonical.json"))
            artifact_loader.assert_called_once_with(Path("automation.json"))
            runner.assert_called_once_with(module_root.resolve(), "python", document, artifact)

    def test_main_keeps_v2_root_as_execution_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_v2(root)
            exit_code, report, document, artifact, _, _, runner = self._run_v3_main(root)

            self.assertEqual(exit_code, 0)
            self.assertEqual(report["verdict"], "PASS")
            runner.assert_called_once_with(root.resolve(), "python", document, artifact)

    def test_main_falls_back_to_project_root_when_manifest_is_absent_with_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exit_code, report, document, artifact, _, _, runner = self._run_v3_main(
                root,
                "--language",
                "python",
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(report["verdict"], "PASS")
            runner.assert_called_once_with(root.resolve(), "python", document, artifact)

    def test_explicit_missing_manifest_or_module_never_falls_back_to_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for arguments in (
                ("--skillsrc", str(root / "missing.skillsrc"), "--language", "python"),
                ("--module", "api", "--language", "python"),
            ):
                with self.subTest(arguments=arguments), patch(
                    "tools.canonical_document.load_canonical_document"
                ) as document_loader, patch.object(
                    run_tests, "load_automation_artifact"
                ) as artifact_loader, patch.object(
                    run_tests, "run_tests_v3"
                ) as runner:
                    exit_code, report = self._run_main(
                        "--project",
                        str(root),
                        "--canonical-document",
                        "canonical.json",
                        "--automation-artifact",
                        "automation.json",
                        *arguments,
                    )

                self.assertEqual(exit_code, 2)
                self.assertEqual(report["error"]["code"], "skillsrc_required")
                self.assertEqual(report["error"]["message"], "Runner input is invalid.")
                document_loader.assert_not_called()
                artifact_loader.assert_not_called()
                runner.assert_not_called()

    def test_main_does_not_bypass_an_unreadable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._write_v2(root)
            with patch.object(run_tests, "load_skillsrc", side_effect=OSError("permission denied")):
                exit_code, report = self._run_main(
                    "--project",
                    str(root),
                    "--skillsrc",
                    str(manifest),
                    "--language",
                    "python",
                    "--canonical-document",
                    "canonical.json",
                    "--automation-artifact",
                    "automation.json",
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["error"]["code"], "RUNNER_INPUT")
            self.assertEqual(report["error"]["message"], "Runner input is invalid.")

    def test_main_requires_language_when_manifest_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exit_code, report = self._run_main(
                "--project",
                str(root),
                "--canonical-document",
                "canonical.json",
                "--automation-artifact",
                "automation.json",
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(report["error"]["code"], "language_required")
            self.assertEqual(report["error"]["message"], "Runner input is invalid.")


if __name__ == "__main__":
    unittest.main()
