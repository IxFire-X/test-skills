import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


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
def runner():
    return load_tool("run_tests")


@pytest.fixture
def trace_check():
    return load_tool("trace_check")


@pytest.fixture
def contract():
    return json.loads((ROOT / "contracts/pipeline.json").read_text(encoding="utf-8"))
