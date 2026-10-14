"""B3: a runtime wrapper that is not there is reported as missing, not as a symlink."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.project_inventory import InventoryError, module_runtime_path


@pytest.mark.parametrize("relative", ["mvnw", "bin/mvnw-local", "tools/wrap/run.sh"])
def test_a_missing_runtime_path_is_reported_as_missing(tmp_path: Path, relative: str) -> None:
    (tmp_path / "tools").mkdir()
    with pytest.raises(InventoryError) as error:
        module_runtime_path(tmp_path, relative)
    assert str(error.value) == f"runtime path does not exist: {tmp_path.resolve() / relative}"


def test_an_existing_runtime_path_is_still_returned(tmp_path: Path) -> None:
    (tmp_path / "mvnw").write_bytes(b"#!/bin/sh\n")
    assert module_runtime_path(tmp_path, "mvnw") == tmp_path.resolve() / "mvnw"
