import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / "tools"


def load_tool(name: str):
    """Execute one tool with local sibling imports, restoring host import state."""
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load tool: {name}")
    module = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    missing = object()
    local_names = (
        "json_cli", "contract_check", "run_tests",
        "tools", "tools.json_cli", "tools.contract_check", "tools.run_tests",
    )
    original_modules = {key: sys.modules.get(key, missing) for key in local_names}
    try:
        sys.path[:] = [str(TOOL_ROOT), str(ROOT), *[entry for entry in original_path if entry not in {str(TOOL_ROOT), str(ROOT)}]]
        for key in local_names:
            sys.modules.pop(key, None)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
        for key, saved in original_modules.items():
            if saved is missing:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved
    return module


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def doctor():
    return load_tool("doctor")


@pytest.fixture
def render_contract_docs():
    return load_tool("render_contract_docs")


@pytest.fixture
def contract_check():
    return load_tool("contract_check")


@pytest.fixture
def runner():
    return load_tool("run_tests")


@pytest.fixture
def scanner():
    return load_tool("scan_project")


@pytest.fixture
def trace_check():
    return load_tool("trace_check")


@pytest.fixture
def contract():
    return json.loads((ROOT / "contracts/pipeline.json").read_text(encoding="utf-8"))
