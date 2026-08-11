import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
import yaml

from tools.doctor import inspect_environment


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def markdown_section(document: str, heading: str) -> str:
    start = document.index(heading)
    next_heading = document.find("\n## ", start + len(heading))
    return document[start:] if next_heading == -1 else document[start:next_heading]


def compact(text: str) -> str:
    return " ".join(text.split())


class OrchestratorSkillsrcBootstrapTests(unittest.TestCase):
    def test_public_docs_lead_with_automatic_initialization(self):
        for relative in ("README.md", "USAGE.md", "HOW-IT-WORKS.md"):
            text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("автомат", text.lower())
            self.assertIn(".skillsrc", text)
        self.assertNotIn(
            "Скопируйте `.skillsrc.example`",
            (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8"),
        )

    def test_doctor_requires_skillsrc_bootstrap_runtime(self):
        report = inspect_environment(REPOSITORY_ROOT)
        self.assertTrue(report["integrity"]["valid"])

    def test_skillsrc_example_is_a_two_module_v3_manifest(self):
        example = yaml.safe_load((REPOSITORY_ROOT / ".skillsrc.example").read_text(encoding="utf-8"))
        schema = json.loads((REPOSITORY_ROOT / "schemas/skillsrc.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(example["version"], "3.0")
        self.assertEqual([module["id"] for module in example["modules"]], ["backend", "frontend"])
        self.assertFalse(list(Draft202012Validator(schema).iter_errors(example)))

    def test_operational_bootstrap_contract_is_ordered_and_isolates_controller_evidence(self):
        skill = (REPOSITORY_ROOT / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8")
        reference = (REPOSITORY_ROOT / "skills/orchestrate/references/orchestration-contract.md").read_text(encoding="utf-8")
        preparation = markdown_section(skill, "## Обязательная подготовка")
        order = markdown_section(reference, "## Исполнимый порядок")
        preparation_contract = compact(preparation)
        order_contract = compact(order)
        initial_receipt = "<project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json"
        answers = "<project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-answers.json"
        second_receipt = "<project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-init.json"
        init = f"python <root>/tools/init_skillsrc.py --project <project> --write --output {initial_receipt}"
        validate = f"python <root>/tools/validate_artifact.py <root>/schemas/skillsrc-init-output.schema.json {initial_receipt}"
        retry = f"python <root>/tools/init_skillsrc.py --project <project> --write --answers {answers} --output {second_receipt}"
        module_selection = "Затем загрузи `<project>/.skillsrc` и выбери exact module ID."
        context_gate = "Не переходи к `context-marker` без exact module selection."

        self.assertIn(initial_receipt, preparation_contract)
        self.assertIn(answers, preparation_contract)
        self.assertIn(second_receipt, preparation_contract)
        self.assertIn(init, order_contract)
        self.assertIn(validate, order_contract)
        self.assertIn(retry, order_contract)
        self.assertLess(order_contract.index(init), order_contract.index(validate))
        self.assertLess(order_contract.index(validate), order_contract.index(module_selection))
        self.assertLess(order_contract.index(module_selection), order_contract.index(context_gate))
        self.assertIn("Покажи только первый неразрешённый вопрос", preparation_contract)
        self.assertIn("не задавай следующий вопрос одновременно", preparation_contract)
        self.assertIn("Не включай discovery questions, answers и bootstrap-квитанции во входы evaluator-скиллов.", preparation_contract)
        self.assertIn("--module <module-id>", order_contract)

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
            schema = json.loads((REPOSITORY_ROOT / "schemas/skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema)
            first_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertFalse(list(validator.iter_errors(first_receipt)))
            self.assertEqual(first_receipt["status"], "created")

            second_receipt = project / "docs/to_do/run/00-project-bootstrap/attempt-02/skillsrc-init.json"
            second_command = command[:-1] + [str(second_receipt)]
            second = subprocess.run(second_command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            unchanged_receipt = json.loads(second_receipt.read_text(encoding="utf-8"))
            self.assertFalse(list(validator.iter_errors(unchanged_receipt)))
            self.assertEqual(unchanged_receipt["status"], "unchanged")

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
            preparation = markdown_section(
                (REPOSITORY_ROOT / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8"),
                "## Обязательная подготовка",
            )
            self.assertIn("останови пайплайн до `context-marker`", preparation)


if __name__ == "__main__":
    unittest.main()
