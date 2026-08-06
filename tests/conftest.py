import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / "tools"

# Dynamic specs execute tool modules outside package-import machinery.  Make their
# sibling helpers and the `tools.*` fallback importable from this pack location,
# never from the pytest current working directory.
for import_root in (str(ROOT), str(TOOL_ROOT)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load tool: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
