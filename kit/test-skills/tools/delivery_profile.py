"""Which optional parts of the package this copy carries.

The host delivery profile (``python -m tools.release_manifest --list host``) leaves out the process launch of builds,
tests and PIT (``tools/process_launch.py``) and the model process runner (``pipeline_driver_runner``,
``pipeline_driver_run``, ``model_runner``).  Without the first, ``execution.mode`` is ``human`` by default and ``auto``
is unavailable; without the second, ``--review-runner process`` and ``run --runner process`` are.  The parts are found
by an ordinary import, never by a module name in a string.  ``import tools.<name>`` (not ``from tools import <name>``):
only that form reports a missing module as ``ModuleNotFoundError`` with its name.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

EXECUTION_MODE_UNAVAILABLE = "EXECUTION_MODE_UNAVAILABLE"
RUNNER_UNAVAILABLE = "RUNNER_UNAVAILABLE"
_OPTIONAL = {
    "tools.process_launch": EXECUTION_MODE_UNAVAILABLE,
    "tools.pipeline_driver_runner": RUNNER_UNAVAILABLE,
    "tools.pipeline_driver_run": RUNNER_UNAVAILABLE,
    "tools.model_runner": RUNNER_UNAVAILABLE,
}
_MESSAGES = {
    EXECUTION_MODE_UNAVAILABLE: ("this copy of the package starts no build, test or mutation process (host delivery): "
                                 "use execution.mode human — a person runs the printed commands"),
    RUNNER_UNAVAILABLE: ("this copy of the package has no model process runner (host delivery): "
                         "the CLI session that leads the run answers the tasks (next/submit)"),
}


class ProfileUnavailable(Exception):
    """A part the request needs is not in this copy of the package."""

    def __init__(self, code: str) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


def _missing(error: ModuleNotFoundError) -> str | None:
    return _OPTIONAL.get(error.name or "")


def process_launch_available() -> bool:
    try:
        import tools.process_launch  # noqa: F401
    except ModuleNotFoundError as error:
        if _missing(error) is None:
            raise
        return False
    return True


def process_runner_available() -> bool:
    try:
        import tools.model_runner  # noqa: F401
        import tools.pipeline_driver_run  # noqa: F401
        import tools.pipeline_driver_runner  # noqa: F401
    except ModuleNotFoundError as error:
        if _missing(error) is None:
            raise
        return False
    return True


def require(code: str) -> None:
    """Stop with ``code`` when the part it names is not in this copy."""
    available = process_launch_available() if code == EXECUTION_MODE_UNAVAILABLE else process_runner_available()
    if not available:
        raise ProfileUnavailable(code)


@contextmanager
def needs(code: str) -> Iterator[None]:
    """Around a lazy import of an optional part: its absence is ``ProfileUnavailable(code)``, never a traceback."""
    try:
        yield
    except ModuleNotFoundError as error:
        if _missing(error) is None:
            raise
        raise ProfileUnavailable(code) from error
