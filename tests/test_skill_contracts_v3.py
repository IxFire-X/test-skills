"""Finite, behavior-bearing static contracts for the V3 skill guidance."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from tools.automation_validation import validate_automation_artifact
from tools.canonical_document import validate_canonical_document
from tools.revision_selection import select_effective_document, validate_successor
from tools.schema_validation import load_json_strict, schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]
OWNED_GUIDANCE = {
    "skills/context-marker/SKILL.md": ("context-marker", "references/context-artifact-contract.md"),
    "skills/context-marker/references/context-artifact-contract.md": None,
    "skills/tc-generator/SKILL.md": ("tc-generator", "references/case-generation-contract.md"),
    "skills/tc-generator/references/case-generation-contract.md": None,
    "skills/tc-reviewer/SKILL.md": ("tc-reviewer", "references/review-verdicts.md"),
    "skills/tc-reviewer/references/review-verdicts.md": None,
    "skills/tc-to-autotest/SKILL.md": ("tc-to-autotest", "references/automation-output-contract.md"),
    "skills/tc-to-autotest/references/automation-output-contract.md": None,
    "skills/tc-to-autotest/assets/java-python-conventions/python-pytest.md": None,
    "skills/tc-to-autotest/assets/java-python-conventions/java-junit5.md": None,
    "skills/autotest-reviewer/SKILL.md": ("autotest-reviewer", "references/autotest-review-contract.md"),
    "skills/autotest-reviewer/references/autotest-review-contract.md": None,
    "skills/orchestrate/SKILL.md": ("orchestrate", "references/orchestration-contract.md"),
    "skills/orchestrate/references/orchestration-contract.md": None,
}
SKILLS = tuple(path for path in OWNED_GUIDANCE if path.endswith("/SKILL.md"))
FORBIDDEN = (
    "test_data",
    "expected_outcome",
    "generated_test_cases",
    "corrected_test_cases",
    "automation_matrix",
    "METHOD-",
    "reviewed_test_case_ids",
)
SCENARIO_IDS = (
    "context-provenance-gap",
    "generator-three-step-order-flow",
    "generator-eleven-step-data-flow",
    "generator-mixed-manual-automatic",
    "generator-missing-technical-context-pressure",
    "reviewer-multicase-full-successor",
    "reviewer-reject-stable-identity-deletion",
    "automation-one-symbol-multiple-targets",
    "autotest-reviewer-complete-pair-coverage",
    "orchestrator-exact-human-markdown",
)
STAGE_SCHEMAS = {
    "context-marker": "schemas/context-marker-output.schema.json",
    "tc-generator": "schemas/tc-generator-output.schema.json",
    "tc-reviewer": "schemas/tc-reviewer-output.schema.json",
}


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class SkillContractsV3Tests(unittest.TestCase):
    def test_exact_owned_inventory_has_frontmatter_and_resolvable_references(self):
        self.assertEqual(14, len(OWNED_GUIDANCE))
        for relative, metadata in OWNED_GUIDANCE.items():
            with self.subTest(relative=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                if metadata is None:
                    continue
                expected_name, reference = metadata
                text = read(relative)
                frontmatter = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: .+?\n---\n", text, re.DOTALL)
                self.assertIsNotNone(frontmatter)
                self.assertEqual(expected_name, frontmatter.group(1))
                self.assertIn(f"]({reference})", text)
                self.assertTrue((path.parent / reference).is_file())

    def test_skills_name_contract_tools_and_nonnegotiable_stop_conditions(self):
        required_tools = {
            "skills/context-marker/SKILL.md": ("canonical_document", "context-marker-output.schema.json"),
            "skills/tc-generator/SKILL.md": ("canonical_document", "tc-generator-output.schema.json"),
            "skills/tc-reviewer/SKILL.md": ("canonical_document", "revision_selection", "tc-reviewer-output.schema.json"),
            "skills/tc-to-autotest/SKILL.md": ("canonical_document", "automation_validation", "execution_preflight"),
            "skills/autotest-reviewer/SKILL.md": ("canonical_document", "automation_validation", "autotest-reviewer-output.schema.json"),
            "skills/orchestrate/SKILL.md": ("Pipeline 2.0", "orchestrate_test_case_revision", "build_trace_document", "trace_check"),
        }
        for relative, terms in required_tools.items():
            with self.subTest(relative=relative):
                text = read(relative).lower()
                for term in terms:
                    self.assertIn(term.lower(), text)
                self.assertRegex(text, r"(?s)останов|stop.*(?:v2\.1|schema|semantic|secret|validator|tool)")
                for stop_term in ("v2.1", "invention", "validator", "schema", "secret", "authorized"):
                    self.assertIn(stop_term, text)

    def test_guidance_has_no_legacy_semantic_model_or_one_step_limit(self):
        corpus = "\n".join(read(path) for path in OWNED_GUIDANCE).lower()
        for token in FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token.lower(), corpus)
        self.assertNotIn("2.1.0", corpus)
        self.assertNotRegex(corpus, r"(?:ровно|exactly|only)\s+(?:one|один)\s+(?:step|шаг)")
        self.assertNotRegex(corpus, r"(?:parse|read)\s+(?:markdown|csv).*automation")

    def test_generator_preserves_unbounded_step_and_human_machine_contract(self):
        text = read("skills/tc-generator/SKILL.md").lower() + read("skills/tc-generator/references/case-generation-contract.md").lower()
        for term in ("arbitrary", "sequential steps", "previous-step", "human action", "expected result", "goal", "preconditions", "assertion", "blocker", "manual_reason", "json-only", "projection"):
            self.assertIn(term, text)

    def test_generator_uses_adaptive_scenario_keys(self):
        text = read("skills/tc-generator/SKILL.md").lower() + read(
            "skills/tc-generator/references/case-generation-contract.md"
        ).lower()
        for term in (
            "one case per independently executable scenario",
            "scenario key",
            "setup/role",
            "initial state",
            "input partition/branch condition",
            "primary action",
            "terminal outcome",
            "cohesive dependent action chain",
            "coverage-only",
            "exactly once",
            "numeric target",
            "assertion is not a case",
            "coverage record is not a case",
        ):
            with self.subTest(term=term):
                self.assertIn(term, text)
        self.assertRegex(text, r"split.+independently executable")
        self.assertRegex(text, r"deduplicat.+same control flow")
        self.assertRegex(text, r"no (?:minimum|maximum|cap|quota|per-domain count)")

    def test_generator_does_not_count_assertions_or_accept_numeric_targets(self):
        text = (read("skills/tc-generator/SKILL.md") + read("skills/tc-generator/references/case-generation-contract.md")).lower()
        for phrase in ("an assertion is not a case", "a coverage record is not a case", "input partition/branch condition"):
            self.assertIn(phrase, text)
        for forbidden in ("minimum case", "maximum case", "per-domain count", "numeric target"):
            self.assertNotIn("accept " + forbidden, text)

    def test_adaptive_case_granularity_inline_contexts_are_valid_context_artifacts(self):
        manifest = json.loads(read("evals/adaptive-case-granularity/scenarios.json"))
        scenarios = manifest["scenarios"]
        self.assertEqual(3, len(scenarios))
        for scenario in scenarios:
            with self.subTest(scenario=scenario["id"]):
                self.assertEqual(
                    [],
                    schema_diagnostics(scenario["input"]["inline_context"], ROOT / STAGE_SCHEMAS["context-marker"], ROOT),
                )

    def test_context_requirement_ids_have_a_reproducible_derivation_rule(self):
        text = read("skills/context-marker/references/context-artifact-contract.md")
        for term in (
            "Unicode NFC",
            "trim",
            "replace `\\` with `/`",
            "CRLF/CR to LF",
            "every remaining whitespace run",
            "(normalized_reference, original_reference)",
            "first ordered distinct requirement gets `REQ-0001`",
            "second gets `REQ-0002`",
            "REQ-####",
            "provenance",
        ):
            self.assertIn(term, text)

    def test_generator_returns_only_canonical_json_without_publishing_projections(self):
        text = read("skills/tc-generator/SKILL.md").lower()
        self.assertNotIn("tools.test_case_projections", text)
        self.assertNotIn("tools.publish_test_case_bundle", text)
        self.assertIn("later", text)
        self.assertIn("json-only", text)

    def test_reviewer_requires_complete_successor_and_identity_preservation(self):
        text = read("skills/tc-reviewer/SKILL.md").lower() + read("skills/tc-reviewer/references/review-verdicts.md").lower()
        for term in ("reviewed_case_ids", "physical order", "complete successor", "identity graph", "lineage", "auto_fix_applied", "требует доработки"):
            self.assertIn(term, text)

    def test_automation_skills_require_atomic_relations_and_complete_pair_coverage(self):
        text = "\n".join((
            read("skills/tc-to-autotest/SKILL.md"),
            read("skills/tc-to-autotest/references/automation-output-contract.md"),
            read("skills/autotest-reviewer/SKILL.md"),
            read("skills/autotest-reviewer/references/autotest-review-contract.md"),
        )).lower()
        for term in ("(file_id, symbol_id)", "atomic operation/assertion", "and", "selected effective canonical document", "manual disposition", "reviewed_symbol_pairs"):
            self.assertIn(term, text)

    def test_language_assets_enforce_preflight_http_boundary_and_atomic_relations(self):
        for relative in (
            "skills/tc-to-autotest/assets/java-python-conventions/python-pytest.md",
            "skills/tc-to-autotest/assets/java-python-conventions/java-junit5.md",
        ):
            with self.subTest(relative=relative):
                text = read(relative).lower()
                for term in ("(file_id, symbol_id)", "global provider/adapter preflight", "one transport attempt", "no implicit redirects", "no retries", "no cookies", "no auth", "no default headers", "no decompression", "atomic operation/assertion"):
                    self.assertIn(term, text)

    def test_orchestrator_uses_exact_v3_lifecycle_commands_and_terminal_statuses(self):
        text = read("skills/orchestrate/SKILL.md").lower() + read("skills/orchestrate/references/orchestration-contract.md").lower()
        for term in ("publish candidate", "publish a valid full successor", "effective revision", "effective digest", "orchestrate_revision", "validate_trace_document", "finalize_orchestration", "run_tests.py", "build_trace_document.py", "trace_check.py", "pass", "pass_with_manual_remainder", "manual_only", "blocked", "fail", "not_runnable"):
            self.assertIn(term, text)
        self.assertIn("do not hand-build the terminal carrier", text)

    def test_discovery_and_secret_sentinels_remain_and_discovery_owned_files_are_unchanged(self):
        sentinels = {
            "skills/context-marker/SKILL.md": "не сканируй посторонние файлы",
            "skills/tc-to-autotest/SKILL.md": "не добавляй зависимости",
            "skills/orchestrate/SKILL.md": "skill_files",
        }
        for relative, sentinel in sentinels.items():
            with self.subTest(relative=relative):
                self.assertIn(sentinel, read(relative).lower())
        changed = subprocess.run(
            ["git", "diff", "--name-only"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.splitlines()
        forbidden_paths = {".skillsrc.example", "schemas/skillsrc.schema.json", "tools/scan_project.py"}
        self.assertFalse(forbidden_paths.intersection(changed))

    def test_synthetic_scenarios_and_rubric_are_closed_and_fail_closed(self):
        manifest = json.loads(read("evals/zephyr-test-case-projection/scenarios.json"))
        self.assertEqual("1.0", manifest["schema_version"])
        self.assertEqual("zephyr-test-case-projection-v3", manifest["suite"])
        self.assertEqual(
            {"variants": ["control", "guidance"], "fresh_context": True, "samples_per_scenario_variant": 5, "persist_raw_outputs": False, "record": "aggregate_failure_categories_only"},
            manifest["sample_policy"],
        )
        scenarios = manifest["scenarios"]
        self.assertEqual(SCENARIO_IDS, tuple(item["id"] for item in scenarios))
        for scenario in scenarios:
            self.assertTrue(scenario["synthetic"])
            self.assertTrue(scenario["skill"])
            self.assertIsInstance(scenario["input"], dict)
            self.assertTrue(scenario["input"])
            self.assertTrue(scenario["input"].get("request"))
            fixture_refs = scenario["input"].get("fixture_refs")
            self.assertIsInstance(fixture_refs, list)
            self.assertTrue(fixture_refs)
            canonical_by_role = {}
            for fixture in fixture_refs:
                self.assertEqual({"role", "path", "kind"}, set(fixture))
                fixture_path = Path(fixture["path"])
                self.assertFalse(fixture_path.is_absolute())
                fixture_value = load_json_strict(ROOT / fixture_path)
                if fixture["kind"] == "canonical":
                    canonical = fixture_value
                else:
                    schema_path = ROOT / STAGE_SCHEMAS[fixture["kind"]]
                    self.assertEqual([], schema_diagnostics(fixture_value, schema_path, ROOT), fixture["path"])
                    canonical = fixture_value.get("artifacts", {}).get("canonical_document")
                if canonical is not None:
                    self.assertEqual([], validate_canonical_document(canonical), fixture["path"])
                    canonical_by_role[fixture["role"]] = canonical
                if fixture["kind"] == "tc-reviewer":
                    report = fixture_value["artifacts"]["validation_report"]
                    candidate = canonical_by_role.get("candidate")
                    self.assertIsNotNone(candidate, "review fixture requires preceding candidate fixture")
                    successor = fixture_value["artifacts"].get("successor_document")
                    if successor is not None:
                        self.assertEqual([], validate_canonical_document(successor), fixture["path"])
                        self.assertEqual([], validate_successor(candidate, successor), fixture["path"])
                    self.assertIsNotNone(select_effective_document(candidate, report, successor))
            if artifact := scenario["input"].get("inline_automation_artifact"):
                canonical = canonical_by_role[scenario["input"]["automation_document_role"]]
                self.assertEqual([], validate_automation_artifact(artifact, canonical), scenario["id"])
            self.assertTrue(scenario["pressure"])
            self.assertNotRegex(
                scenario["pressure"].lower(),
                r"\b(?:do not|must|should|return|reject|emit|preserve|all|only|never|without|"
                r"canonical|validator|projection|blocker|assertion|successor|identity|pair|manual)\b",
            )
            self.assertTrue(scenario["coverage_tags"])
            self.assertTrue(scenario["oracle"])
        rubric = read("evals/zephyr-test-case-projection/rubric.md").lower()
        for term in ("hard binary", "schema validity", "canonical semantic validity", "scenario topology/identity", "exact human format", "no invented facts/secrets", "exact stop behavior", "repeated bypass fails"):
            self.assertIn(term, rubric)


if __name__ == "__main__":
    unittest.main()
