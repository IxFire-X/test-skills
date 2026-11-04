"""P8a-1: the one seam between the core and the code that builds, runs tests and contacts the stand.

Everything that starts a build or a test process, or opens a connection to the stand, lives in the four ``tools/exec_*.py``
files; the core never imports them.  The profile of a delivery is the presence of those files — a file cannot be "enabled"
without copying the code — so a delivery without them is cases-only, and ``local-pilot-v1`` is not offered by it.

This module is the only place that loads a module by name (``find_spec`` and ``import_module``), and only ``tools.<name>`` for
a name in the closed list ``EXEC_MODULES``.
"""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

EXEC_MODULES = ("exec_commands", "exec_stand", "exec_probe", "exec_runner")
LOCAL_PILOT = "local-pilot-v1"
CASES_ONLY = "cases-only-v1"


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


def load(name: str) -> ModuleType | None:
    """The execution module ``name`` (one of ``EXEC_MODULES``), or None when this delivery does not have the file."""
    if name not in EXEC_MODULES:
        raise ValueError(f"not an execution module: {name!r}")
    if not _present(name):
        return None
    return importlib.import_module("tools." + name)
