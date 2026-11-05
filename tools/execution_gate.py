"""P8a-1: the one seam between the core and the code that builds, runs tests and contacts the stand.

Everything that starts a build or a test process, or opens a connection to the stand, lives in the four ``tools/exec_*.py``
files; the core never imports them.  The profile of a delivery is the presence of those files — a file cannot be "enabled"
without copying the code — so a delivery without them is cases-only, and ``local-pilot-v1`` is not offered by it.

The nine modules of mode B (generation and run of automated tests) are the same kind of thing: a delivery of cases does not carry
them, and the core reaches one only through ``load_mode_b`` — the module, or ``ModeBMissing`` with a code and a fix.

This module is the only place that loads a module by name (``find_spec`` and ``import_module``), and only ``tools.<name>`` for
a name in a closed list (``EXEC_MODULES``, ``LEGACY_MODULES``, ``MODE_B_MODULES``).
"""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType
from typing import Any

EXEC_MODULES = ("exec_commands", "exec_stand", "exec_probe", "exec_runner", "exec_git")
# F4b: the way into the review of before (the CLI of the nine commands) is a file of the development build only
LEGACY_MODULES = ("review_legacy",)
MODE_B_MODULES = ("run_tests", "generated_delta", "automation_validation", "execution_adapters", "autotest_hints", "trace_check",
                  "build_trace_document", "quarantine", "code_slices")
LOCAL_PILOT = "local-pilot-v1"
CASES_ONLY = "cases-only-v1"


class ModeBMissing(RuntimeError):
    """A module of mode B was asked for in a delivery that does not carry it."""

    code = "MODE_B_NOT_IN_PACKAGE"


def _present(name: str) -> bool:
    try:
        return importlib.util.find_spec("tools." + name) is not None
    except (ImportError, ValueError):
        return False


def execution_available() -> bool:
    """Whether the package has all of its execution files."""
    return all(_present(name) for name in EXEC_MODULES)


def default_profile() -> str:
    """The profile a delivery runs by default: what its files can do."""
    return LOCAL_PILOT if execution_available() else CASES_ONLY


def legacy_available() -> bool:
    """Whether the package has the old CLI of the review (``tools/review_legacy.py``): the build of development, not a delivery of cases."""
    return all(_present(name) for name in LEGACY_MODULES)


def mode_b_available() -> bool:
    """Whether the package has all nine modules of mode B."""
    return all(_present(name) for name in MODE_B_MODULES)


def _load_closed(name: str, allowed: tuple[str, ...], what: str) -> ModuleType | None:
    if name not in allowed:
        raise ValueError(f"not {what}: {name!r}")
    if not _present(name):
        return None
    return importlib.import_module("tools." + name)


def load_legacy(name: str) -> ModuleType | None:
    """The module ``name`` (one of ``LEGACY_MODULES``), or None when this delivery does not have the file."""
    return _load_closed(name, LEGACY_MODULES, "a module of the old review path")


def load(name: str) -> ModuleType | None:
    """The execution module ``name`` (one of ``EXEC_MODULES``), or None when this delivery does not have the file."""
    return _load_closed(name, EXEC_MODULES, "an execution module")


def load_mode_b(name: str) -> ModuleType:
    """The module ``name`` of mode B (one of ``MODE_B_MODULES``); ``ModeBMissing`` when this delivery does not have the file."""
    module = _load_closed(name, MODE_B_MODULES, "a module of mode B")
    if module is None:
        raise ModeBMissing(f"в этой поставке нет модуля режима Б tools/{name}.py")
    return module


def from_mode_b(name: str, *attributes: str) -> tuple[Any, ...]:
    """What ``from tools.<name> import a, b`` would give, for a module of mode B: ``a, b = from_mode_b("<name>", "a", "b")``."""
    module = load_mode_b(name)
    return tuple(getattr(module, attribute) for attribute in attributes)
