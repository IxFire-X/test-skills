"""The one way the package starts ``git`` (S11 C9).

A repository can name programs git runs on its own (``core.fsmonitor``, external diff and textconv drivers),
and the caller's ``GIT_*`` variables can point git at another repository or inject configuration
(``GIT_DIR``, ``GIT_CONFIG_PARAMETERS``, ``GIT_EXTERNAL_DIFF``).  So every call turns fsmonitor off, starts
without any inherited ``GIT_*``, never prompts for credentials, reads no stdin and has a timeout.  Git's
ownership check (``safe.directory``) stays in force: a repository owned by someone else is reported as
unavailable instead of being trusted.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

PREFIX = ("git", "-c", "core.fsmonitor=false")


def git_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in (os.environ if source is None else source).items() if not key.upper().startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def run_git(arguments: Sequence[str], *, cwd: Path | str | None = None, timeout: int, text: bool = False) -> Any:
    """``subprocess.run`` of ``git -c core.fsmonitor=false <arguments>``; OSError/SubprocessError are the caller's."""
    return subprocess.run([*PREFIX, *arguments], cwd=None if cwd is None else str(cwd), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=git_environment(), check=False,
                          **({"text": True} if text else {}))
