from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "human_gates: keep the package defaults of the human gates (S3, S5) instead of turning them off")


@pytest.fixture(scope="session")
def pack_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _human_gates_off_for_recorded_runs():
    """The human gates are on by default (decision of 2026-10-19); runs on fixtures and records turn them off explicitly.

    Session-wide, so module fixtures that drive whole runs see it too.  A test of the gates themselves is marked
    ``human_gates`` and sees the package defaults (``_human_gates_defaults``).
    """
    from tools import human_gates

    patch = pytest.MonkeyPatch()
    patch.setattr(human_gates, "DEFAULT_EXECUTION_REVIEW", "none")
    patch.setattr(human_gates, "DEFAULT_CONFIRM_CONTEXT", False)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def _human_gates_defaults(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("human_gates") is None:
        return
    from tools import human_gates

    monkeypatch.setattr(human_gates, "DEFAULT_EXECUTION_REVIEW", "human")
    monkeypatch.setattr(human_gates, "DEFAULT_CONFIRM_CONTEXT", True)
