import hashlib
import importlib.machinery
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / "tools"
TOOL_NAMESPACE = f"_portable_skill_tools_{hashlib.sha256(str(ROOT.resolve()).encode('utf-8')).hexdigest()[:16]}"


def _namespace_entries() -> dict[str, object]:
    """Return the complete isolated-tool namespace snapshot."""
    prefix = f"{TOOL_NAMESPACE}."
    return {name: module for name, module in sys.modules.items() if name == TOOL_NAMESPACE or name.startswith(prefix)}


def _clear_namespace() -> None:
    for name in _namespace_entries():
        sys.modules.pop(name, None)


def _new_namespace_package() -> types.ModuleType:
    package = types.ModuleType(TOOL_NAMESPACE)
    package.__package__ = TOOL_NAMESPACE
    package.__path__ = [str(TOOL_ROOT)]
    spec = importlib.machinery.ModuleSpec(TOOL_NAMESPACE, loader=None, is_package=True)
    spec.submodule_search_locations = [str(TOOL_ROOT)]
    package.__spec__ = spec
    return package


def _namespace_is_local(snapshot: dict[str, object]) -> bool:
    package = snapshot.get(TOOL_NAMESPACE)
    if package is None:
        return True
    paths = getattr(package, "__path__", None)
    if paths is None:
        return False
    try:
        return [Path(path).resolve() for path in paths] == [TOOL_ROOT.resolve()]
    except TypeError:
        return False


def _assert_local_namespace_entries() -> None:
    for name, module in _namespace_entries().items():
        if name == TOOL_NAMESPACE:
            continue
        source = getattr(module, "__file__", None)
        if source is None or Path(source).resolve().parent != TOOL_ROOT.resolve():
            raise ImportError(f"Isolated tool namespace contains non-local module: {name}")


def load_tool(name: str):
    """Load one tool in a fresh, private namespace without host import state."""
    if not name.isidentifier():
        raise ValueError(f"Invalid tool module name: {name}")
    path = ROOT / "tools" / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(path)
    snapshot = _namespace_entries()
    if not _namespace_is_local(snapshot):
        raise ImportError(f"Refusing hostile isolated tool namespace: {TOOL_NAMESPACE}")
    target_name = f"{TOOL_NAMESPACE}.{name}"
    spec = importlib.util.spec_from_file_location(target_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load tool: {name}")
    try:
        _clear_namespace()
        sys.modules[TOOL_NAMESPACE] = _new_namespace_package()
        module = importlib.util.module_from_spec(spec)
        sys.modules[target_name] = module
        spec.loader.exec_module(module)
        _assert_local_namespace_entries()
    except Exception:
        _clear_namespace()
        sys.modules.update(snapshot)
        raise
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
