from pathlib import Path

import pytest

from tools import run_lock

run_lock.disable()  # the run lock belongs to the commands, not to the fixtures; the subprocesses of the tests keep it


@pytest.fixture(scope="session")
def pack_root() -> Path:
    return Path(__file__).resolve().parents[1]
