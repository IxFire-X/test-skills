import pytest
from pathlib import Path


if Path.cwd().resolve() != Path(__file__).resolve().parents[1]:
    collect_ignore = ["test_existing.py"]


@pytest.fixture
def conftest_value() -> str:
    return "from-conftest"
