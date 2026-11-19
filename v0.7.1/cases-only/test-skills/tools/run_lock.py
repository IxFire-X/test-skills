"""One OS lock per run for every command that touches it, and the typed refusals of the journal.

``pilot_state._run_root`` — the one door to a run — calls ``hold``; the lock lives to the end of the process (the OS frees it when the
process dies, a killed one included), a second call for the same run does nothing.  What is locked is a byte behind the end of the run's
``run-manifest.json`` — a file every run has from its creation and nothing rewrites — opened for reading by the rules of ``confined_output``;
no file is made.  A run without its manifest is no run: nothing is locked and the command refuses it by itself.  The lock is on by default;
only ``tests/conftest.py`` calls ``disable()``."""

from __future__ import annotations

import os
import time
from pathlib import Path

from tools.confined_output import open_confined_lock_descriptor

WAIT_SECONDS = 30.0  # a test sets the attribute in its own process; there is no other switch
_POLL_SECONDS = 0.05
_WINDOWS_LOCK_OFFSET = 1 << 30  # a locked byte cannot be read on Windows: the one that is locked lies far behind the end of the manifest
_ENABLED = True
_HELD: dict[str, int] = {}


class JournalRefusal(ValueError):
    """A refusal of the run journal with its own ``code`` (``CONCURRENT_WRITER``, ``JOURNAL_CORRUPT``, ``PUBLICATION_UNKNOWN``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def disable() -> None:
    global _ENABLED
    _ENABLED = False


def _try_lock(descriptor: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def hold(project: Path, root: Path) -> None:
    """Take the lock of the run ``root`` for the rest of the process; wait ``WAIT_SECONDS``, then refuse ``CONCURRENT_WRITER``."""
    key = str(root)
    if not _ENABLED or key in _HELD:
        return
    descriptor = open_confined_lock_descriptor(project, root, Path(root) / "run-manifest.json")
    if descriptor is None:
        return
    deadline = time.monotonic() + WAIT_SECONDS
    while not _try_lock(descriptor):
        if time.monotonic() >= deadline:
            os.close(descriptor)
            raise JournalRefusal("CONCURRENT_WRITER", "another command is working with this run")
        time.sleep(_POLL_SECONDS)
    _HELD[key] = descriptor
