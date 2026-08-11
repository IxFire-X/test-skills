import json
import re

FORBIDDEN_TEXT = ("assert True", "placeholder assertion", "╨", "╤", "тАФ", "�")
FORBIDDEN_TEST_CODE = ("TODO", "FIXME", "Thread.sleep", "time.sleep")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_e2e_artifacts_have_no_placeholders_or_mojibake(root):
    artifact_root = root / "docs/to_do/e2e"
    for path in artifact_root.rglob("*"):
        if path.suffix.lower() not in {".json", ".md", ".py", ".java"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in FORBIDDEN_TEXT), path


def test_generated_tests_have_exact_case_markers_and_real_assertions(root):
    expectations = {
        "java": {
            "source": "StudentControllerRealChainTest.java",
            "method_pattern": r"(?m)^\s*@Test\s*$",
            "assertion": ".andExpect(",
        },
        "python": {
            "source": "test_real_chain.py",
            "method_pattern": r"(?m)^\s+def test_",
            "assertion": "self.assert",
        },
    }

    for language, expected in expectations.items():
        language_root = root / "docs/to_do/e2e" / language
        acceptance = _load(language_root / "acceptance.json")
        test_case_artifact = next(
            item for item in acceptance["artifacts"] if item["role"] == "test_cases"
        )
        test_cases = _load(root / test_case_artifact["path"])["artifacts"][
            "generated_test_cases"
        ]["test_cases"]
        source = (language_root / "generated" / expected["source"]).read_text(
            encoding="utf-8"
        )

        assert not any(token in source for token in FORBIDDEN_TEST_CODE)
        assert len(re.findall(expected["method_pattern"], source)) == len(test_cases)
        assert source.count(expected["assertion"]) >= len(test_cases)
        for test_case in test_cases:
            assert source.count(test_case["id"]) == 1
