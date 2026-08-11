from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "autotest-reviewer" / "SKILL.md"
REFERENCE = ROOT / "skills" / "autotest-reviewer" / "references" / "autotest-review-contract.md"
FIXTURES = ROOT / "skills" / "autotest-reviewer" / "assets" / "autotest-fixtures"


def test_reviewer_uses_language_native_traceability_and_stack_rules():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required in (
        "Определить язык/framework по manifest",
        "Java/Kotlin — display name",
        "Python — имя, marker или parameter ID",
        "Go — `Test...`, table/subtest name",
        "TypeScript/JavaScript — `test`/`it` title",
        "Не применять Java-only правила к другим языкам",
    ):
        assert required in text

    for stale_universal_rule in (
        "соответствующий `@DisplayName`",
        "между исходными ТК/аналитикой и Java-кодом",
        "Сгенерированный Java-код автотестов",
        "кросс-валидации с `@DisplayName`",
    ):
        assert stale_universal_rule not in text


def test_reviewer_checks_project_native_runtime_setup_not_only_test_shape():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required_policy in (
        "roles, permissions, fixtures, setup hooks, client/application initialization",
        "завершиться 401/403 до проверяемого поведения",
        "Do not accept a base class name as sufficient evidence",
    ):
        assert required_policy in text


def test_reviewer_independently_checks_behavior_and_rejects_missing_or_extra_tests():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required_policy in (
        "independently reconstruct",
        "setup → action → test data → oracle",
        "missing test case",
        "extra executable test",
        "oracle that the generated harness cannot produce",
        "Do not trust tc-to-autotest for assertion semantics",
        "Do not modify application or project files",
    ):
        assert required_policy in text

    assert "Логическая корректность assert'ов | Доверяем `tc-to-autotest`" not in text


def test_package_uses_portable_review_contract_and_fixtures_without_legacy_docs():
    package = SKILL.parent

    assert REFERENCE.is_file()
    assert (FIXTURES / "valid-project-native.py.txt").is_file()
    assert (FIXTURES / "defective-runtime-setup.py.txt").is_file()
    assert not (package / "README.md").exists()
    assert not (package / "examples.md").exists()
