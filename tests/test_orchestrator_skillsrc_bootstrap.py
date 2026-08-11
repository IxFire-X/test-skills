import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class OrchestratorSkillsrcBootstrapTests(unittest.TestCase):
    def test_bootstrap_precedes_context_marker_and_run_tests_uses_module(self):
        skill = (REPOSITORY_ROOT / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8")

        bootstrap = skill.index("tools/init_skillsrc.py")
        context = skill.index("context-marker")

        self.assertLess(bootstrap, context)
        self.assertIn("--module <module-id>", skill)
        self.assertIn("needs_input", skill)
        self.assertIn("не задавай следующий вопрос одновременно", skill)

    def test_end_to_end_missing_skillsrc_creates_v3_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "pyproject.toml").write_text(
                '[project]\nname="api"\ndependencies=["fastapi", "pytest"]\n', encoding="utf-8"
            )
            (project / "src").mkdir()
            (project / "tests").mkdir()
            receipt = project / "docs/to_do/run/00-project-bootstrap/attempt-01/skillsrc-init.json"
            command = [
                sys.executable,
                str(REPOSITORY_ROOT / "tools/init_skillsrc.py"),
                "--project", str(project),
                "--write",
                "--output", str(receipt),
            ]

            first = subprocess.run(command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(yaml.safe_load((project / ".skillsrc").read_text(encoding="utf-8"))["version"], "3.0")
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["status"], "created")

            second_receipt = project / "docs/to_do/run/00-project-bootstrap/attempt-02/skillsrc-init.json"
            second_command = command[:-1] + [str(second_receipt)]
            second = subprocess.run(second_command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second_receipt.read_text(encoding="utf-8"))["status"], "unchanged")

    def test_ambiguous_package_stops_before_context_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "package.json").write_text(
                '{"name":"web","devDependencies":{"jest":"1","mocha":"1"}}', encoding="utf-8"
            )
            receipt = project / "docs/to_do/run/00-project-bootstrap/attempt-01/skillsrc-init.json"
            process = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/init_skillsrc.py"),
                    "--project", str(project),
                    "--write",
                    "--output", str(receipt),
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(process.returncode, 3, process.stdout + process.stderr)
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["status"], "needs_input")
            self.assertFalse((project / ".skillsrc").exists())
            skill = (REPOSITORY_ROOT / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("останови пайплайн до `context-marker`", skill)


if __name__ == "__main__":
    unittest.main()
