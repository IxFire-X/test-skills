from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPOSITORY_ROOT / "skills" / "tc-to-autotest" / "SKILL.md"
REFERENCE = REPOSITORY_ROOT / "skills" / "tc-to-autotest" / "references" / "automation-output-contract.md"
JAVA_ASSET = REPOSITORY_ROOT / "skills" / "tc-to-autotest" / "assets" / "java-python-conventions" / "java-junit5.md"
PYTHON_ASSET = REPOSITORY_ROOT / "skills" / "tc-to-autotest" / "assets" / "java-python-conventions" / "python-pytest.md"


def test_auth_generation_requires_portable_runtime_credentials_only():
    raw_skill_text = SKILL.read_text(encoding="utf-8")
    skill_text = " ".join(raw_skill_text.split())
    reference_text = " ".join(REFERENCE.read_text(encoding="utf-8").split())

    for required_policy in (
        "Never copy, emit, or persist bearer/session/API tokens, passwords",
        "cookies, private keys, fixture secrets, or credentials",
        "подтверждённый runtime helper",
        "механизм secret injection",
        "Не изобретать `testToken`, `ApiConfig`, фиктивного пользователя или fallback credential",
        "вернуть blocking diagnostic вместо догадки",
    ):
        assert required_policy in skill_text

    for required_contract in (
        "безопасный auth mechanism отсутствует",
        "обязательный runtime setup неизвестен",
        "Не выпускать success envelope",
    ):
        assert required_contract in reference_text


def test_java_generation_prefers_confirmed_project_native_test_architecture():
    asset_text = " ".join(JAVA_ASSET.read_text(encoding="utf-8").split())
    skill_text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required_policy in (
        "Найти ближайший тест того же endpoint, компонента или слоя",
        "подтверждённую архитектуру проекта сильнее любого примера",
        "`@WebMvcTest + MockMvc`",
        "не требуют выдуманного `BaseApiTest`",
        "Базовый класс сам по себе не доказывает",
    ):
        assert required_policy in skill_text

    assert "только когда пользователь выбрал Java/JUnit 5" in asset_text
    assert "Не создавать `BaseApiTest`" in asset_text


def test_existing_project_generation_preserves_required_runtime_setup_seams():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required_policy in (
        "roles и permissions",
        "fixtures и factories",
        "setup/teardown hooks",
        "client/application initialization",
        "endpoint-specific доступ настроен",
    ):
        assert required_policy in text


def test_generation_is_project_confined_and_never_falls_back_to_another_stack():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())

    for required_policy in (
        "Do not modify application source, existing tests, project configuration, manifests, lockfiles, or dependencies",
        "Write only the declared generated test companions",
        "Never fall back to Java or another language when the project stack is unresolved or unsupported",
        "return a blocking diagnostic",
        "JSON envelope is the sole machine authority",
    ):
        assert required_policy in text

    assert "fallback на `java-junit5.md`" not in text


def test_package_uses_portable_reference_and_assets_without_legacy_docs_or_templates():
    package = SKILL.parent

    assert REFERENCE.is_file()
    assert JAVA_ASSET.is_file()
    assert PYTHON_ASSET.is_file()
    assert not (package / "README.md").exists()
    assert not (package / "examples.md").exists()
    assert not (package / "templates").exists()
