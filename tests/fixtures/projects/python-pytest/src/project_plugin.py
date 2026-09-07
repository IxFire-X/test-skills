import pytest


@pytest.fixture
def plugin_value() -> str:
    return "from-project-plugin"
